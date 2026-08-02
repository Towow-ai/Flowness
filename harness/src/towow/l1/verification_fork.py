"""RUN-039: blocking independent verification fork — spec 强制的"独立验证" runtime.

# spec source:
#   04-l1-intelligence/M-1.4-execution-skill-detailed-design.md §6.2 (execution-self-check
#     fork — OPUS, "Executor 不能 self-assess——必须我(独立 OPUS fork)来跑 self_check") +
#     失败模式#10 ("self-check 走过场 = 不跑独立 OPUS fork 就 self-assess = 禁止")
#   04-l1-intelligence/M-1.4 §6.1 (advisor-consult fork — 独立 OPUS 决策者)
#   04-l1-intelligence/M-1.6-fix-skill-detailed-design.md §6.1 (fix-self-check fork —
#     "独立性保证不让 main session 自欺欺人" + tools 无 Edit/Write V-02)
#   04-l1-intelligence/M-1.2-engineering-consensus-skill-detailed-design.md §9.3 (6 forks)

同步阻塞的独立 OPUS 子会话 (`claude -p`): spawn → 阻塞等退出 → 解析结构化 verdict 读回。

# spawn 形态 (owner 2026-06-29 拍 — R12『fork 层切换先不做』回退):
#   R12/T-LRF-07 曾把 REAL fork 从 `claude -p` headless 迁到 `claude --bg` 订阅内可见
#   (理由: 计费落订阅 + `claude agents` 可见)。owner 复核判定该『包外计费』驱动不成立、不值
#   上线风险 → REAL/默认 fork **回退 headless `claude -p`** (本次单发同步, 解析 stdout verdict)。
#   BG_POLLED 模式 + bg_polled_runner_factory 基础设施保留 (verify-observer R07 仍用), 但
#   REAL/默认生产路径不再 spawn `claude --bg`。
区别于 l2.claude_bg_helper.spawn_bg_session (detached `claude --bg` 接力 relay; 本模块是只读 verdict fork)。

被 on-demand 自检 / advisor / 共识 / 审计 / resolver fork 从 CLI 命令直接调用。**与 orchestrator
轮询循环 / paused.flag / watermark 零连接** —— 开这些 fork 不会连带触发 backlog 自动派发 (RUN-039
红线护栏 #2, 见 test_run039_spawn_autodispatch_separation)。

独立性结构保证:
  - 独立进程 (claude -p): 验的人在全新 context, 看不到主 session 的执行过程, 只读 artifacts
    (capsule 投喂的 task contract / envelope draft / diff)。
  - --disallowed-tools Edit Write NotebookEdit: 移除三个专用写工具 (V-02 防自欺)。**边界如实
    (f-r12-4)**: 这移除的是专用写工具, 不等于"fork 物理上不能改代码"——保留 Bash 的 fork
    (READ_ONLY_TOOLS 含 Bash; BG_POLLED 又用 bypassPermissions) 仍可经 Bash `printf > file` 写文件。
    对这类 fork, containment 靠 prompt 完整性 + fork 只读约定 + sandbox, 不靠 --disallowed-tools
    单独成立; 只有 allowed_tools=() 的 fork (如 resolver headless real 路径) 才真无任何写能力。
  - 能 disprove: verdict.passed=False → caller fail-closed, 不放行。
  - fail-closed: spawn 起不来 / 超时 / verdict 解析失败 → 不静默放行 (不留'永远走 fallback 内联'假路径)。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import TypeGuard

from towow.l1.memory_admission import fork_memory_admission

# 只读验证工具 (跟现有 fork SKILL.md `tools:` 声明一致)。
READ_ONLY_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob", "Bash")
# V-02 防自欺: 这三个专用写工具被 --disallowed-tools 移除。边界如实 (f-r12-4): 这不等于"fork
# 物理上不能改代码"——READ_ONLY_TOOLS 含 Bash, 保留 Bash 的 fork (尤其 BG_POLLED bypassPermissions)
# 仍可经 Bash `printf > file` 写文件; 对这类 fork containment 靠 prompt 完整性 + 只读约定 + sandbox,
# 非 --disallowed-tools 单独保证。allowed_tools=() 的 fork (resolver headless real) 才真无写能力。
# 清单只能含 Claude Code CLI **当前已知**的工具名 — 传未知名作 deny 规则会被 CLI 硬拒
# (`Permission deny rule "X" matches no known tool`) → 非零退出 → fork 永不 spawn →
# 整个独立验证层 fail-closed 死电路 (finding-verification-fork-multiedit-disallowed-spawn-1)。
# 一个曾存在、当前 CLI 已移除的旧批量编辑写工具因此从清单剔除 (工具已不存在, 无需 disallow);
# Edit/Write/NotebookEdit 是三个真写工具, 仍在清单内 — V-02 不削弱。
FORBIDDEN_TOOLS: tuple[str, ...] = ("Edit", "Write", "NotebookEdit")
DEFAULT_MODEL = "opus"
MODEL_SONNET = "sonnet"
# 复杂验证余量 (≥480s; RUN-042 Cap3)。可被参数 / 环境变量 TOWOW_VERIFICATION_FORK_TIMEOUT_S 覆盖。
DEFAULT_TIMEOUT_S = 900
# 基础设施类失败 (非零退出 / spawn OSError) 默认 retry 次数 (RUN-042 Cap2); 0 = 关闭重试。
DEFAULT_MAX_RETRIES = 1

# BG_POLLED fork (verify-observer R07) 的 --bg 权限模式 (保留供 bg_polled 基础设施用)。
# V-02 由 --disallowed-tools 独立保证 (与权限模式正交); 但 fork 需 Bash 落 verdict 产物 + 跑
# grep, `auto`/`acceptEdits` 会卡 Bash 批准 (后台无人应答 → 永久卡, claude_bg_helper 实证),
# 故用 bypassPermissions (= 主对话 bg job 既有配置)。
BG_FORK_PERMISSION_MODE = "bypassPermissions"
# 注入 fork prompt 的结果回收契约: bg 子会话经 Bash 把结构化 verdict 写到这个 drop-file。
# {drop} 是绝对路径; 双花括号转义 printf 例子里的 JSON 花括号。
_DROP_INSTRUCTION_TEMPLATE = (
    "\n\n---\n"
    "[结果回收契约 fork-spawn-bg-subscription-contract@v1] 完成判断后, **用 Bash 工具**把你"
    "最终的结构化 JSON verdict (且只有那段 JSON, 不要解释/不要 markdown 代码围栏) 写到这个绝对路径:\n"
    "  {drop}\n"
    "例如: printf '%s' '{{\"passed\": true, ...}}' > {drop}\n"
    "这是主调方**唯一**的结果回收口 — 不写这个文件 = 验证超时 fail-closed (不放行)。"
    "不要写任何其他文件 (Edit/Write 已被物理移除, 只可经 Bash 写此 drop-file)。"
)

# ── fork-orchestration-resilience@v1 clause ④ 模型分层 ──────────────────────────────
# 决策 / 审计 fork = OPUS; 机械验证 fork = SONNET 级。配置锚点 = DEFAULT_MODEL +
# run_verification_fork(model=) 参数 (model=None → 按本表解析; 显式传 model → 调用方覆盖)。
# 分类原则: 抓假/判断密度高的【门】fork (self-check / audit / verify-step / advisor) 留 OPUS —
# 绝不为省钱削弱 disprove 能力 (与 anti-fake-done-gate-fail-closed 同精神: 验证质量优先);
# 真正机械的【非阻塞】fork (verify-observer 重算 transcript 真值核对探针) = SONNET 够用。
# 未登记的 fork_skill_id → DEFAULT_MODEL (保守: 判断密度未知按高能力档, 不省到判断质量上)。
_MODEL_TIER_MAP: dict[str, str] = {
    # 决策 / 审计 (OPUS) —— 门 fork, 判断密度高
    "advisor-consult": DEFAULT_MODEL,
    "audit": DEFAULT_MODEL,
    "execution-self-check": DEFAULT_MODEL,
    "fix-self-check": DEFAULT_MODEL,
    "verify-step": DEFAULT_MODEL,
    # 机械验证 (SONNET 级) —— 非阻塞、确定性重算核对
    "verify-observer": MODEL_SONNET,
}

# ── fork-orchestration-resilience@v1 clause ⑤ fork 层并发帽 ─────────────────────────
# 一个编排进程内【同时在跑的验证 fork 数】上限 (防 spawn 风暴)。**与 execution-dispatch 会话帽
# 分层并存, 不重定义**: execution_dispatch.DEFAULT_CAP_TOTAL/OPUS/SONNET (b77ad3e) 管的是
# 『同时几个执行 SESSION 工位 (claude --bg 真派发)』; 本帽管的是『一个会话/编排内同时几个验证
# FORK (claude -p / --bg 子调用)』。两个不同层各自独立, 谁也不覆盖谁的常量。
DEFAULT_FORK_CONCURRENCY_CAP = 4
_ENV_FORK_CONCURRENCY_CAP = "TOWOW_FORK_CONCURRENCY_CAP"

_ENV_MODE = "TOWOW_VERIFICATION_FORK_MODE"
_ENV_REPLAY_FILE = "TOWOW_VERIFICATION_FORK_REPLAY_FILE"
_ENV_TIMEOUT = "TOWOW_VERIFICATION_FORK_TIMEOUT_S"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# markdown code fence (```json ... ``` / ``` ... ```) 起始标记 — 剥掉只留 fence 内 JSON。
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\n?")
# verdict passed 字段被认作"通过 / 未通过"的字符串形态 (容忍 fork 把 bool 输出成字符串)。
_TRUTHY_STR = frozenset({"true", "yes", "passed", "pass", "ok"})
_FALSY_STR = frozenset({"false", "no", "failed", "fail"})
# 递归定位 passed 键的最大下钻深度 (防病态深嵌 / 循环引用消耗)。
_LOCATE_MAX_DEPTH = 8


class ForkMode(StrEnum):
    """验证 fork 运行模式."""

    REAL = "real"  # 真起 claude -p headless 独立子会话 (built-in _default_runner, 解析 stdout verdict)
    REPLAY = "replay"  # 从文件读 verdict (测试 / 捕获重放; spawned=False)
    DISABLED = "disabled"  # 不 spawn — caller 自己处理 (须显式降级 + 记账, 不静默放行)
    BG_POLLED = "bg_polled"  # R07/R12: claude --bg 可见后台会话 + 轮询 + caller 注入自己的 spawn (e.g. verify-observer)


class ForkSpawnError(RuntimeError):
    """独立验证 fork 基础设施失败 (claude 不可用 / 超时 / 非零退出 / 模式禁用).

    fail-closed: caller 收到此异常应阻塞放行, 不得静默回退内联自评假装独立。
    """


class ForkTimeoutError(ForkSpawnError):
    """独立验证 fork 超时 (RUN-042 Cap2/Cap3).

    ForkSpawnError 子类 —— 既有 `except ForkSpawnError` caller 行为不变 (仍 fail-closed)。
    单独成类是为了让 retry 层区分"超时" (默认不 retry: 重试只会再等一个 timeout, 解药是更长
    默认 timeout) vs "非零退出 / spawn 失败" (瞬态, 默认 retry once)。

    partial_stdout (f-fork-timeout-retry-sessionid-collision, criterion 2): 超时收割器
    (subprocess timeout) 杀进程前已从子进程 stdout 读到的内容。fork 可能在被杀的瞬间已产出
    完整合法 verdict —— 那份产出不该随 kill 丢弃。_default_runner 只【搬运】它 (不判 schema,
    它不知 result_key), _spawn_loop 收到超时后判是否含合法 verdict 决定收割还是落回 resume/retry。
    默认 "" (既有 `raise ForkTimeoutError(msg)` 与注入 runner 的直接 raise 零回归)。
    """

    def __init__(self, *args: object, partial_stdout: str = "") -> None:
        super().__init__(*args)
        self.partial_stdout = partial_stdout


class ForkMemoryAdmissionError(ForkSpawnError):
    """fork spawn 被内存门拒 (口子4 补防线 / per-session-fork-uncounted-by-cap-causes-oom@v1).

    系统可用内存有界等待后仍 < 阈值 → 真起 `claude -p` fork 前拒绝, 防 per-session fork 把进程
    顶到 55G OOM。ForkSpawnError 子类 → 既有 `except ForkSpawnError` caller 行为不变 (门 fork
    fail-closed 不放行, 安全侧)。**单独成类是为了让 _spawn_loop 的 retry 层【短路不重试】**: 门内
    已做过有界延迟, retry 只会再叠一次延迟、白占会话内存, 且内存不会在一个 retry 周期内可靠回落
    —— 解药是【内存回落后整条任务重派】(orchestrator 在内存恢复后重新派发), 不是当下死等重试。
    """


@dataclass(frozen=True)
class ForkRunResult:
    """一次 fork 子进程执行的原始结果."""

    returncode: int
    stdout: str
    stderr: str


# (argv, cwd, timeout_s) -> ForkRunResult — 可注入缝 (单元测试假 fork verdict 输出, 不真 spawn).
ForkRunner = Callable[[Sequence[str], Path, int], ForkRunResult]


@dataclass(frozen=True)
class ForkUsage:
    """clause ⑥ token 预算: 一次 fork 子调用的 token / 成本用量 (从 claude json envelope 抽)."""

    input_tokens: int = 0  # 含 cache_creation / cache_read (全归输入侧)
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ForkVerdict:
    """独立验证 fork 的结构化裁决 — caller 据 passed 决定放行/阻塞."""

    passed: bool  # gate 相关的 bool (self_check_result.passed / verdict 允许放行)
    result: dict[str, object]  # 完整结构化结果 (self_check_result / advisor verdict)
    spawned: bool  # True iff 真起了独立子进程 (False = replay/注入)
    fork_skill_id: str
    model: str
    command_text: str = ""
    exit_code: int = 0
    stdout_excerpt: str = ""
    error: str | None = None  # 非 None = 验证未通过的原因 (解析失败 / fork 自报 fail)
    per_check: list[dict[str, object]] = field(default_factory=list)
    retried: bool = False  # True iff 基础设施失败后【从零 retry】过 (RUN-042 Cap2, 便于统计瞬态率)
    resumed: bool = False  # fork-crash-checkpoint-resume@v1: True iff 崩溃后从 fork 会话存档断点
    # `claude --resume <session-id>` 续跑过 (取代整支从零重跑; 与 retried 正交 —— resume 是续、retry 是重来)
    from_journal: bool = False  # clause ① True iff verdict 来自 journal 缓存命中 (未重 spawn)
    harvested_from_timeout: bool = False  # f-fork-timeout-retry-sessionid-collision criterion 2:
    # True iff 本 verdict 从【超时被杀但 stdout 已含合法 verdict】的进程收割而来 (非干净退出)。
    # provenance 诚实: 审计链据此知道它不是正常收口, 而是收割了超时瞬间已产出的合法 verdict。
    usage: ForkUsage = field(default_factory=ForkUsage)  # clause ⑥ 本次 token / 成本用量
    expand_degraded: str | None = None  # debt-0529dd0a268c: 非 None = 展开族本想继承父会话却降级了
    # 零继承的原因 (parent 非 Claude UUID / 带 --resume 的 spawn 失败退零继承重试)。None = 无降级
    # (真继承了 / gate 族 / 本就无 parent)。provenance 诚实: 降级不静默 —— caller 据此记账/告知,
    # Inline degradation follows the same audit rule: persist the downgrade in
    # the ledger instead of silently bypassing independent verification.


# ────────────────────────────────────────────────────────────────────────────────
#  fork-orchestration-resilience@v1 primitives (clause ①③④⑤⑥)
#
#  借鉴 Claude Code Workflow 的 fork 编排韧性约定 (owner 点名), 所有 fork 调用方共享前提。
#  这些是 run_verification_fork 的可选协作件 —— 调用方不传则行为跟现状完全一致 (零回归)。
# ────────────────────────────────────────────────────────────────────────────────


# ── clause ④ 模型分层 ──────────────────────────────────────────────────────────────
def model_for_fork(fork_skill_id: str, *, default: str = DEFAULT_MODEL) -> str:
    """clause ④ 模型分层: 决策/审计 fork → OPUS, 机械验证 fork → SONNET。未登记 → default(OPUS)。

    这是 run_verification_fork(model=None) 的默认解析源 (见 _MODEL_TIER_MAP); 调用方显式传
    model 时覆盖之 (config 锚点不变: DEFAULT_MODEL + model= 参数)。
    """
    return _MODEL_TIER_MAP.get(fork_skill_id, default)


# ── clause ③ 失败语义 (null 不炸编排 vs 门类 fail-closed) ─────────────────────────────
class ForkFailurePolicy(StrEnum):
    """clause ③ 单 fork 失败/超时时调用方该怎么办。"""

    GATE_FAIL_CLOSED = "gate_fail_closed"  # 门: 死/超时 = 不放行 (异常上抛, caller fail-closed)
    CONSULT_NULL_TOLERABLE = "consult_null_tolerable"  # 咨询/研究: 死 = null, 不炸编排


# clause ③ 门成员资格 = 单一可信来源 (single source of truth): **门 = 非 _CONSULT_FORKS 的补集**
# (default-to-gate)。唯一登记的清单是下面的 _CONSULT_FORKS (咨询/研究 fork); 其余一切 fork
# (含未登记的) 一律按门 fail-closed —— 与 anti-fake-done-gate-fail-closed 不变量【同口径】。
#
# 已知门 fork: execution-self-check / fix-self-check / audit / verify-step —— 死/超时/无有效
# verdict → 对应的门一律判 not-pass, 绝不 fail-open 放过。它们的门资格**不靠一份显式枚举驱动**
# (那会变成与 _CONSULT_FORKS 静默漂移的死清单, 且诱导 fail-open 重构), 而是由下方 default-to-gate
# 策略 + 回归测试 test_gate_forks_fail_closed / test_unknown_fork_defaults_to_gate 共同钉死。
#
# ⚠ 绝不把 fork_failure_policy 改成 `if fork_skill_id in <某门白名单>: gate else consult` 的白名单
# 形态 —— 那会让任何未登记 fork 立刻翻成 CONSULT=fail-open, default-to-gate 的安全网消失
# (F-LRF09-gate-forks-deadlist)。门资格永远走"非 _CONSULT_FORKS"的补集判定。
# clause ③ 咨询/研究 fork —— 失败返回 null, caller 显式处理 (不当通过, 但编排不崩可重试/升级)。
# advisor 失败 = null (caller 不能假装拿到决策, 但可重 consult / escalate); verify-observer =
# 非阻塞治理抽查, 死了那次跳过不拦主链。
_CONSULT_FORKS: frozenset[str] = frozenset({"advisor-consult", "verify-observer"})


def fork_failure_policy(fork_skill_id: str) -> ForkFailurePolicy:
    """clause ③ 判别 fork_skill_id → 失败语义。

    门成员资格的**单一可信来源**: 门 = 非 _CONSULT_FORKS 的补集 (default-to-gate)。
    咨询/研究 fork (登记在 _CONSULT_FORKS) → CONSULT_NULL_TOLERABLE; 其余一切 (含未登记) →
    GATE_FAIL_CLOSED (保守默认: 不确定是不是门就当门, 宁可 fail-closed 阻塞, 绝不把未知 fork 的
    失败误当 null 放过 —— 与 anti-fake-done 同精神)。**不改成 `in <门白名单>` 形态** (会让未登记
    fork fail-open, 见上方 _CONSULT_FORKS 注释 / F-LRF09-gate-forks-deadlist)。
    """
    if fork_skill_id in _CONSULT_FORKS:
        return ForkFailurePolicy.CONSULT_NULL_TOLERABLE
    return ForkFailurePolicy.GATE_FAIL_CLOSED


# ── clause ⑤ fork 层并发帽 ──────────────────────────────────────────────────────────
def resolve_fork_concurrency_cap(explicit: int | None = None) -> int:
    """clause ⑤ 并发帽解析: explicit 参数 > env TOWOW_FORK_CONCURRENCY_CAP > 默认。非正 → 默认。"""
    if explicit is not None and explicit > 0:
        return explicit
    raw = os.environ.get(_ENV_FORK_CONCURRENCY_CAP)
    if raw is not None:
        try:
            val = int(raw.strip())
        except ValueError:
            val = 0
        if val > 0:
            return val
    return DEFAULT_FORK_CONCURRENCY_CAP


class ForkConcurrencyCap:
    """clause ⑤ fork 层并发帽: 限制同时在跑的验证 fork 数 (防 spawn 风暴)。

    与 execution-dispatch 会话帽 (execution_dispatch.DEFAULT_CAP_TOTAL/OPUS/SONNET, b77ad3e)
    【分层并存, 不重定义】——那管『同时几个执行 SESSION 工位』, 这管『一个编排内同时几个验证
    FORK』, 各自独立。slot() 是阻塞 context manager (BoundedSemaphore): 满了就等不丢调用;
    in_flight / available 可查。
    """

    def __init__(self, cap: int | None = None) -> None:
        self._cap = resolve_fork_concurrency_cap(cap)
        self._sem = threading.BoundedSemaphore(self._cap)
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def cap(self) -> int:
        return self._cap

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    @property
    def available(self) -> int:
        with self._lock:
            return self._cap - self._in_flight

    @contextmanager
    def slot(self) -> Iterator[None]:
        self._sem.acquire()
        with self._lock:
            self._in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1
            self._sem.release()


# ── clause ⑥ token 预算 (编排级累计可查) ─────────────────────────────────────────────
def extract_fork_usage(stdout: str) -> ForkUsage:
    """clause ⑥: 从 `claude -p --output-format json` 输出抽 token / 成本用量。

    claude envelope 带 `usage` (input_tokens / output_tokens / cache_creation_input_tokens /
    cache_read_input_tokens) + `total_cost_usd`; 数组形态 (新版 CLI) 这些在尾部 result 元素上
    (取最后一个带 usage/total_cost_usd 的)。抽不到 → 全 0 (用量未知不该炸编排)。
    """
    clean = _ANSI_RE.sub("", stdout).strip()
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        return ForkUsage()
    if isinstance(parsed, dict):
        envelopes = [parsed]
    elif isinstance(parsed, list):
        envelopes = [el for el in parsed if isinstance(el, dict)]
    else:
        return ForkUsage()
    for env in reversed(envelopes):
        usage_raw = env.get("usage")
        cost_raw = env.get("total_cost_usd")
        if not isinstance(usage_raw, dict) and not isinstance(cost_raw, (int, float)):
            continue
        in_tok = 0
        out_tok = 0
        if isinstance(usage_raw, dict):
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                v = usage_raw.get(k)
                if isinstance(v, int):
                    in_tok += v
            v = usage_raw.get("output_tokens")
            if isinstance(v, int):
                out_tok = v
        cost = float(cost_raw) if isinstance(cost_raw, (int, float)) else 0.0
        return ForkUsage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost)
    return ForkUsage()


@dataclass
class ForkTokenBudget:
    """clause ⑥ 编排级累计 token 预算, 可查。

    跨多次 fork 子调用累计 token / 成本; snapshot() 暴露当前累计供编排层查询。可选 limit_usd
    上限 + over_budget() 让编排层自己决定超预算时停 (本层只可查不强制, 强制策略留给调用方)。
    """

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    fork_count: int = 0
    limit_usd: float | None = None

    def add(self, usage: ForkUsage) -> None:
        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_cost_usd += usage.cost_usd
        self.fork_count += 1

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def remaining_usd(self) -> float | None:
        if self.limit_usd is None:
            return None
        return self.limit_usd - self.total_cost_usd

    def over_budget(self) -> bool:
        return self.limit_usd is not None and self.total_cost_usd > self.limit_usd

    def snapshot(self) -> dict[str, object]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "fork_count": self.fork_count,
            "limit_usd": self.limit_usd,
            "remaining_usd": self.remaining_usd(),
            "over_budget": self.over_budget(),
        }


# ── clause ① journal / resume 断点续跑 ──────────────────────────────────────────────
def fork_cache_key(
    *, fork_skill_id: str, prompt: str, model: str, result_key: str, passed_key: str,
) -> str:
    """clause ① journal 缓存键: 对决定 verdict 的输入 (skill + prompt + model + 解析键) 取稳定
    sha256。**未变更前缀 → 同 key → 缓存命中**, 编排重启不重 spawn 不重花钱。
    """
    h = hashlib.sha256()
    for part in (fork_skill_id, model, result_key, passed_key, prompt):
        h.update(b"\x00")
        h.update(part.encode("utf-8"))
    return h.hexdigest()


class ForkJournal:
    """clause ① 编排级 journal: 已完成 fork 子调用结果落盘 (JSONL append-only); 编排崩溃/重启后
    同 cache_key 直接缓存命中读回 verdict, 不重 spawn。

    只记**产出了有效 verdict 的完成** (passed True/False 都是有效判决) —— 基础设施失败 (spawn
    死 / 超时 / 不可解析) 不落 journal (那些该重跑, 不该被缓存成"完成", 否则把瞬态失败钉死成永久
    缓存就破了 fail-closed)。lookup 取同 key 最后一条 (重记可 supersede)。
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def lookup(self, cache_key: str) -> ForkVerdict | None:
        if not self._path.is_file():
            return None
        hit: dict[str, object] | None = None
        for raw_line in self._path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("cache_key") == cache_key:
                hit = rec
        if hit is None:
            return None
        usage_rec = hit.get("usage")
        usage = (
            ForkUsage(
                input_tokens=int(usage_rec.get("input_tokens", 0)),
                output_tokens=int(usage_rec.get("output_tokens", 0)),
                cost_usd=float(usage_rec.get("cost_usd", 0.0)),
            )
            if isinstance(usage_rec, dict)
            else ForkUsage()
        )
        result = hit.get("result")
        per_check = hit.get("per_check")
        error = hit.get("error")
        exit_code_raw = hit.get("exit_code", 0)
        return ForkVerdict(
            passed=bool(hit.get("passed")),
            result=result if isinstance(result, dict) else {},
            spawned=False,
            fork_skill_id=str(hit.get("fork_skill_id", "")),
            model=str(hit.get("model", "")),
            command_text=str(hit.get("command_text", "")),
            exit_code=exit_code_raw if isinstance(exit_code_raw, int) else 0,
            error=error if isinstance(error, str) else None,
            per_check=per_check if isinstance(per_check, list) else [],
            from_journal=True,
            usage=usage,
        )

    def record(self, cache_key: str, verdict: ForkVerdict) -> None:
        rec = {
            "cache_key": cache_key,
            "fork_skill_id": verdict.fork_skill_id,
            "passed": verdict.passed,
            "result": verdict.result,
            "model": verdict.model,
            "command_text": verdict.command_text,
            "exit_code": verdict.exit_code,
            "error": verdict.error,
            "per_check": verdict.per_check,
            "usage": {
                "input_tokens": verdict.usage.input_tokens,
                "output_tokens": verdict.usage.output_tokens,
                "cost_usd": verdict.usage.cost_usd,
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ────────────────────────────────────────────────────────────────────────────────
#  Command construction
# ────────────────────────────────────────────────────────────────────────────────


def build_fork_command(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    allowed_tools: Sequence[str] = READ_ONLY_TOOLS,
    forbidden_tools: Sequence[str] = FORBIDDEN_TOOLS,
    output_format: str = "json",
    permission_mode: str = "auto",
    add_dirs: Sequence[str] = (),
    session_id: str | None = None,
    extra_args: Sequence[str] = (),
) -> list[str]:
    """构造 `claude -p` headless 独立验证 fork 命令.

    session_id: fork-crash-checkpoint-resume@v1 —— 预分配 fork 【自身】会话 id (须 valid UUID)。
    传则加 `--session-id <uuid>`, 让 fork 用我们指定的 session id 建会话 → 崩溃 (基础设施失败/超时)
    后 caller 可用 `claude --resume <uuid>` 从其 JSONL 会话存档断点续跑 (取代整支从零重跑)。None =
    不预分配 (向后兼容: 展开族已由 extra_args 带 `--resume <parent>` 继承父会话, 不另分配自身 id;
    BG_POLLED/REPLAY 各有自己的会话机制)。

    extra_args: 追加到 argv 末尾的原样 spawn 参数 —— 统一入口 dispatch_fork 按族路由的通道。
    展开族 (expand) 由入口传 `["--resume", <parent-session-id>, "--fork-session"]` 在 `-p` 之上
    获得父会话上下文继承 (fork-unified-dispatch-entry@v1 spawn 形态钉死; 精确继承机制归
    fork-context-mode-by-family@v1)。默认 () = 零继承 (gate 族现状), 向后兼容不改任何既有调用。

    --disallowed-tools 移除 Edit/Write/NotebookEdit 三个专用写工具 (V-02 防自欺)。**边界如实
    (f-r12-4)**: 这不构成"fork 不能改代码"的 containment——allowed_tools 默认含 Bash, Bash 是通用
    写原语 (`printf > file`), 故对保留 Bash 的 fork, 移除专用写工具≠不能写文件; containment 靠
    prompt 完整性 + fork 只读约定 + sandbox。只有调用方传 allowed_tools=() (如 resolver) 才真无写能力。
    --permission-mode auto 让只读工具(Read/Grep/Glob/Bash)在无人 attach 的 headless 下不卡确认。
    (owner 2026-06-04: 产出的 session 一律 auto, 不留 acceptEdits 等其他默认。)
    """
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", output_format]
    if session_id is not None:
        # fork-crash-checkpoint-resume@v1: 预分配 fork 自身 session-id (须 valid UUID) —— 崩溃后
        # 用 `claude --resume <session_id>` 从会话存档断点续跑。放在靠前固定位置 (extra_args 末尾的
        # 展开族 `--resume`/`--fork-session` 位置不变; 现有测试均以 index 定位 flag, 零回归)。
        cmd += ["--session-id", session_id]
    if allowed_tools:
        cmd += ["--allowed-tools", *allowed_tools]
    if forbidden_tools:
        cmd += ["--disallowed-tools", *forbidden_tools]
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    for d in add_dirs:
        cmd += ["--add-dir", d]
    cmd += list(extra_args)
    return cmd


def command_text(argv: Sequence[str]) -> str:
    """审计痕迹字符串 (shell-quoted)."""
    return " ".join(shlex.quote(a) for a in argv)


# ────────────────────────────────────────────────────────────────────────────────
#  claude availability + default subprocess runner
# ────────────────────────────────────────────────────────────────────────────────


def claude_headless_available() -> bool:
    """`claude agents --json` exit 0 = claude CLI 可用 (同 claude_bg_helper 判定)."""
    try:
        result = subprocess.run(
            ["claude", "agents", "--json"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


# ── fork-verdict-dropfile-convention@v1: collision-proof 路径 schema + 用后即删 ──────────
# 三段唯一键里的 fork_skill_id / session_id 是外来字符串 — 净化成 [A-Za-z0-9._-] 防路径穿越
# (session_id="../../etc/x" 不能逃出 fork-verdict/ 目录) + 防文件名非法字符。**uuid 段才是真正的
# collision 保证** (每实例唯一); 前两段只为可追溯 + 穿越安全 (advisor R12: 别过度净化, uuid 兜底)。
_DROPFILE_SEGMENT_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_dropfile_segment(seg: str) -> str:
    """路径段净化: 非 [A-Za-z0-9._-] 字符 → '_', 去前后导 '.' (防 '..' 穿越 / 隐藏文件)。空 → 'x'。"""
    cleaned = _DROPFILE_SEGMENT_UNSAFE_RE.sub("_", seg).strip(".")
    return cleaned or "x"


def fork_verdict_dir(repo_dir: Path) -> Path:
    """fork-verdict-dropfile-convention@v1 落盘目录的**单一真相源** (path schema SSOT)。

    ``<repo>/.towow/tmp/fork-verdict/``。new_collision_proof_drop_file (caller 落盘口) 与
    M-2.2 GC 孤儿回收 (towow.l0.snapshot.gc.sweep_orphan_fork_verdict_dropfiles) 都经此函数取目录,
    两处不会因有人改 schema 而漂移 —— 漂了 GC 就静默扫空目录, orphan 永不被清 (正是这类 SSOT 要堵的
    "looks closed, isn't" 失败; finding f-r12-4-dropfile-orphan-leak-in-repo-tree)。
    """
    return repo_dir / ".towow" / "tmp" / "fork-verdict"


def new_collision_proof_drop_file(
    repo_dir: Path, fork_skill_id: str, session_id: str,
) -> Path:
    """fork-verdict-dropfile-convention@v1 路径 schema (collision-proof):
    ``<repo>/.towow/tmp/fork-verdict/<fork_skill_id>-<session_id>-<uuid>.json``

    三段唯一键 (fork 技能 id + 调用会话 id + 每实例 uuid) 保证并发多 fork / 同一 fork 多次调用
    绝不撞同一文件 —— uuid 是真 collision 保证, 前两段为可追溯 + 路径穿越安全 (经 _sanitize)。
    spawn 前 mkdir drop-file 目录; caller 在『解析成功 / 超时 / fail-closed』三态后均即刻 unlink
    (见 l2.claude_bg_helper.run_bg_polled_gate_fork)。绝对路径 → bg 会话 cwd 不定也写得到。

    隐式第 4 态 (超时 fail-closed 后慢 detached fork 才落盘的 orphan, caller unlink 已 no-op 错过)
    不靠本函数回收, 由 M-2.2 GC 周期清扫兜底 (gc.sweep_orphan_fork_verdict_dropfiles, 复用本模块
    fork_verdict_dir 同一目录)。
    """
    base = fork_verdict_dir(repo_dir)
    base.mkdir(parents=True, exist_ok=True)
    skill_seg = _sanitize_dropfile_segment(fork_skill_id)
    session_seg = _sanitize_dropfile_segment(session_id)
    return base / f"{skill_seg}-{session_seg}-{uuid.uuid4().hex}.json"


def drop_verdict_instruction(drop_file: Path) -> str:
    """BG_POLLED fork prompt 里的 verdict 落盘指令 (caller 拼进 fork prompt)。

    复用 _DROP_INSTRUCTION_TEMPLATE —— 让 fork 经 Bash 把结构化 JSON verdict 写到 drop_file
    (唯一回收口; 不写 = 轮询超时 fail-closed 不放行)。drop_file 须绝对路径 (fork cwd 不定也写得到)。
    """
    return _DROP_INSTRUCTION_TEMPLATE.format(drop=drop_file)


def _default_runner(argv: Sequence[str], cwd: Path, timeout_s: int) -> ForkRunResult:
    """真跑 `claude -p` 子进程, 阻塞等退出.

    口子4 补防线 (per-session-fork-uncounted-by-cap-causes-oom@v1): 真起 `claude -p` fork 前过
    内存 admission 门 —— 这是【所有 headless fork 的真咽喉】(REAL 自检/审计/method/verify-step +
    l0 obligation resolver 都经此函数), 故在此一处拦截即覆盖全部命名的 OOM fork 路径。内存有界等
    待后仍紧 → ForkMemoryAdmissionError (门 fork fail-closed 不放行, 安全侧; _spawn_loop 短路不
    重试)。fail-open: 采不到内存 → 放行 (口子4 派发门 + 口子2 会话数闸兜底)。注入 runner 的路径
    (单测假进程 / BG_POLLED claude --bg) 不经本函数, 各自单独覆盖 / 零回归。
    """
    reject = fork_memory_admission()
    if reject is not None:
        raise ForkMemoryAdmissionError(reject)
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # criterion 2 (harvest-on-timeout): 收割器杀进程前已读到的 stdout 随异常带出 —— 进程可能
        # 恰在被杀瞬间已产出完整合法 verdict, 那份产出交 _spawn_loop 判 (它才知 result_key/schema)。
        # text=True → exc.stdout 为 str; 防御性兜底 bytes/None。这里只搬运, 不判 verdict 合法性。
        raw: object = exc.stdout  # stub 收窄不稳; text=True 下运行时为 str, 仍防御 bytes/None
        if isinstance(raw, bytes):
            partial = raw.decode("utf-8", "replace")
        elif isinstance(raw, str):
            partial = raw
        else:
            partial = ""
        msg = f"verification fork timed out after {timeout_s}s"
        raise ForkTimeoutError(msg, partial_stdout=partial) from exc
    except (FileNotFoundError, OSError) as exc:
        msg = f"verification fork subprocess failed: {exc!r}"
        raise ForkSpawnError(msg) from exc
    return ForkRunResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


# ── R07/R12 非 headless fork: bg 会话 + 轮询 verdict 落盘 ───────────────────────────
def poll_verdict_dropfile(
    drop_file: Path,
    timeout_s: int,
    *,
    poll_interval_s: float = 2.0,
    liveness_probe: Callable[[], bool] | None = None,
    _now: Callable[[], float] | None = None,
    _sleep: Callable[[float], None] | None = None,
) -> str:
    """轮询 verdict 落盘文件直到出现且非空, 返回其内容; 超时 **fail-closed** (ForkTimeoutError)。

    这是非 headless fork 完成检测的核心 (advisor R12 标的风险点): claude --bg 是异步、拿不回
    同步 stdout, 所以约定 fork 把结构化 verdict 写到确定产物文件, runner 阻塞轮询它。**绝不**
    用 assess_vitality (verify fork 无 canonical work-product, 那套判据对它退化, 会永不 DONE);
    也**绝不**因等不到就静默返回 (那就破了 fail-closed)。注入 _now/_sleep 便于单测。

    liveness_probe (T-LRF-08 subprocess-heartbeat-stuck-detection@v1): 可选探针, 返回 bg fork
    子进程是否仍存活 (pid 在)。给定时每轮**先查 drop-file 再探活** (确保进程刚死前落的 verdict
    不丢) —— 探到进程已死 (返回 False) 且仍无 verdict → 立刻 **fail-closed** (ForkSpawnError),
    不再盲等满 timeout。这是 subprocess-heartbeat 的 dead 态用在 fork 验证场景: 死 fork 早 fail-closed,
    不是降级放行 (passed 仍 False)。探针只判 '死/活' (dead → 停等); stuck (活但卡) 仍由 timeout 兜底,
    探针本身派生自同源信号 (job state / 最近活动), 由调用方注入 (subprocess_heartbeat 建)。
    """
    import time as _time

    now = _now or _time.monotonic
    sleep = _sleep or _time.sleep
    deadline = now() + timeout_s
    while now() < deadline:
        if drop_file.is_file():
            content = drop_file.read_text(encoding="utf-8").strip()
            if content:
                return content
        # 先 drop-file 后探活: 进程可能在落 verdict 后立即退出, 那条 verdict 不该被探活判死丢掉。
        if liveness_probe is not None and not liveness_probe():
            msg = (
                f"bg-polled verification fork 子进程已死 (liveness_probe=False) 且无 verdict drop-file "
                f"{drop_file} — fail-closed (dead fork 早退, 绝不静默放行)"
            )
            raise ForkSpawnError(msg)
        sleep(poll_interval_s)
    msg = (
        f"bg-polled verification fork timed out after {timeout_s}s waiting for verdict drop-file "
        f"{drop_file} — fail-closed (绝不静默放行)"
    )
    raise ForkTimeoutError(msg)


def bg_polled_runner_factory(
    drop_file: Path,
    *,
    spawn: Callable[[], object],
    poll_interval_s: float = 2.0,
    liveness_probe: Callable[[], bool] | None = None,
) -> ForkRunner:
    """造一个 ForkRunner: spawn 一个可见 bg 会话 (caller 注入 spawn, 内部用 spawn_bg_session) →
    轮询 drop_file 拿 verdict → ForkRunResult。超时/spawn 失败 fail-closed。

    与 _default_runner 同签名 (argv, cwd, timeout_s)->ForkRunResult, 故可走 REAL 路径的 retry +
    parse_fork_verdict 不变。spawn 解耦注入 (真实 = spawn_bg_session(prompt,...); 单测 = 假 spawn)。

    liveness_probe (T-LRF-08): 可选, 透传给 poll_verdict_dropfile —— 探到 bg fork 子进程已死且
    无 verdict 时早 fail-closed (不盲等满 timeout)。None = 原行为 (只靠 timeout 兜底)。
    """

    def _runner(argv: Sequence[str], cwd: Path, timeout_s: int) -> ForkRunResult:
        try:
            spawn()  # 起可见后台会话 (claude --bg); fork prompt 已约定把 verdict 写 drop_file
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            msg = f"bg-polled fork spawn failed: {exc!r}"
            raise ForkSpawnError(msg) from exc
        stdout = poll_verdict_dropfile(
            drop_file, timeout_s, poll_interval_s=poll_interval_s, liveness_probe=liveness_probe,
        )
        return ForkRunResult(returncode=0, stdout=stdout, stderr="")

    return _runner


# ────────────────────────────────────────────────────────────────────────────────
#  Verdict parsing
# ────────────────────────────────────────────────────────────────────────────────


def _strip_fences(text: str) -> str:
    """剥掉 markdown code fence 标记 (```json ... ``` → 里面内容)。

    只去 fence 标记本身, 不动 JSON 内容。用于让 fence 包裹的 JSON 能被直接 json.loads /
    被平衡括号扫描看到。fence 内 JSON 的 `{` `}` 不在 fence 标记里, 不受影响。
    """
    return _FENCE_RE.sub("", text).replace("```", "")


_JSON_DECODER = json.JSONDecoder()


def _scan_json_objects(text: str) -> list[dict[str, object]]:
    """抽出 text 里所有 `{...}` 且能 parse 成 dict 的 JSON object (散文/前后缀被跳过).

    用真 JSON 解析器 `JSONDecoder.raw_decode` 从每个 `{` 位置起解 —— 替代朴素括号扫描。
    朴素扫描的致命缺陷 (RUN-042 收尾实测根因, M-0.4 / 部分 M-0.1 抽样): 它用 in_str 状态
    跟踪 `"` 来忽略字符串内的 `{` `}`, 但 fork 散文里夹的代码片段含未闭合双引号 (如
    `f"evt-{uuid.uuid4().hex}`) 会打乱 in_str 状态 —— 扫到真 verdict 的 `{` 时误以为在
    字符串内 → 跳过真 verdict → "缺 passed 键"。raw_decode 是真 JSON 语法解析, 只关心从
    `{` 起的合法 JSON, 前面散文的引号/花括号一律不影响。

    返回按出现顺序的 dict 列表 (caller 通常取最后一个 = 最终 verdict)。
    """
    objs: list[dict[str, object]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = _JSON_DECODER.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            objs.append(obj)
            i = end  # 跳过已解析的整段 (避免重复/嵌套重扫)
        else:
            i += 1
    return objs


def _coerce_passed(raw: object) -> bool | None:
    """把 verdict 的 passed 值归一成 bool. 不可识别 → None (caller 视作没定位到有效 verdict)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in _TRUTHY_STR:
            return True
        if s in _FALSY_STR:
            return False
    return None


def validate_verdict_schema(
    verdict_obj: dict[str, object],
    *,
    passed_key: str = "passed",
    required_result_fields: Sequence[str] = (),
) -> str | None:
    """clause ② schema 强制结构化校验: 校验已定位的 verdict 结构是否合法。

    合法 = (1) passed_key 可归一成 bool (有有效判决); (2) required_result_fields 全在 verdict 里。
    返回 None = 合法; 非 None = 错误字符串 (调用方据此重试)。

    **关键不变量 (绝不 retry 真 disprove)**: passed=False 是【合法判决】, schema 视为合法 —— 校验
    只看 passed 能不能归一成 bool, 不看它的真假值。所以一个结构完整但 passed=False 的 disprove
    永远过 schema 校验 (→ 直接返回不重试)。只有【结构残缺】(passed 归一不出 / 缺必需字段) 才算
    schema 失败触发重试 (clause ②: 根治 CLI 升级输出形态漂移, 不是把有效否决当噪音重刷)。
    """
    if _coerce_passed(verdict_obj.get(passed_key)) is None:
        return f"verdict 缺可识别 `{passed_key}` 键 (结构不符 — schema invalid)"
    missing = [f for f in required_result_fields if f not in verdict_obj]
    if missing:
        return f"verdict 缺必需字段 {missing} (schema invalid)"
    return None


def _locate_verdict(
    obj: dict[str, object],
    result_key: str,
    passed_key: str,
    _depth: int = 0,
) -> tuple[dict[str, object], bool] | None:
    """在一个 dict 里定位 verdict (含可识别 passed) → (verdict_dict, passed) 或 None.

    优先级: (1) result_key 包裹层里有 passed; (2) 顶层有 passed; (3) 递归下钻进子 dict
    (容忍 fork 多包了一层 wrapper, 如 verification.self_check_result.passed —— RUN-041 形态 E)。
    只下钻 dict 值, 不进 list (避免误抓 per-check 列表里的 passed 当 verdict)。
    """
    if _depth > _LOCATE_MAX_DEPTH:
        return None
    sub = obj.get(result_key)
    if isinstance(sub, dict) and passed_key in sub:
        p = _coerce_passed(sub[passed_key])
        if p is not None:
            return sub, p
    if passed_key in obj:
        p = _coerce_passed(obj[passed_key])
        if p is not None:
            return obj, p
    for v in obj.values():
        if isinstance(v, dict):
            found = _locate_verdict(v, result_key, passed_key, _depth + 1)
            if found is not None:
                return found
    return None


def _absorb_envelope(whole: dict[str, object], candidates: list[dict[str, object]]) -> None:
    """把一个 claude envelope dict 吸收进候选: 自身 + .result 文本里藏的结构化 verdict."""
    candidates.append(whole)
    res = whole.get("result")
    if isinstance(res, str):
        stripped = _strip_fences(res)
        try:
            rj = json.loads(stripped.strip())
        except json.JSONDecodeError:
            rj = None
        if isinstance(rj, dict):
            candidates.append(rj)
        candidates.extend(_scan_json_objects(stripped))


def _gather_candidates(stdout: str) -> list[dict[str, object]]:
    """从 fork stdout 收集所有候选 verdict dict (claude envelope 外层 + .result 内层 + 散落块)."""
    clean = _ANSI_RE.sub("", stdout)
    candidates: list[dict[str, object]] = []

    # 1. 整段 stdout 直接是 JSON (claude envelope, 或 replay 文件里直接是 verdict)
    try:
        whole = json.loads(clean.strip())
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        # claude envelope (旧版单 result 形态): .result 是最终文本, 结构化 verdict 藏在里面
        _absorb_envelope(whole, candidates)
    elif isinstance(whole, list):
        # finding-fork-verdict-parse-claude-json-array-1: claude CLI 新版 `--output-format
        # json` 返回**消息数组** [{"type":"system","subtype":"init",...}, ...,
        # {"type":"result","result":"<final text>"}], 不再是单 result envelope。verdict 藏在
        # 尾部 result 元素的 .result 文本里; 数组形态下 verdict JSON 在原始 stdout 里是
        # 转义字符串 (\" 引号), 兜底的平衡括号扫描解不出 → 加固前整层 fork fail-closed
        # ("缺 passed 键")。逐元素按 envelope 吸收, 顺序保留 — caller 取 reversed,
        # 尾部 result 元素的 verdict 优先。
        for el in whole:
            if isinstance(el, dict):
                _absorb_envelope(el, candidates)

    # 2. 平衡括号扫描整段 (兜底: 非 envelope / 前后有散文 / 多块)
    candidates.extend(_scan_json_objects(_strip_fences(clean)))
    return candidates


def parse_fork_verdict(
    stdout: str,
    *,
    result_key: str,
    passed_key: str = "passed",
) -> tuple[dict[str, object], bool, str | None]:
    """解析 fork 的结构化 verdict 输出 → (result_dict, passed, error).

    T-LRF-07 后 verdict 来自 drop-file (bg fork 经 Bash 写的 JSON); 历史上也兼容 claude envelope
    形态 {"type":"result","result":"<final text>",...} (verdict 藏 `.result` 文本里)。RUN-042 Cap1
    加固: 容忍 fork 在 verdict 外夹散文 / 包 markdown fence / 多个 JSON 块 / 多包一层 wrapper /
    passed 输出成字符串 —— 从混排里稳健抽出 verdict (drop-file 内容也走同一抽取, 鲁棒性不变)。

    result_key: verdict 在结果 dict 里的键 (如 "self_check_result"); 顶层就是 verdict 也认。
    passed_key: verdict dict 里表示通过与否的键 (bool 或 "true"/"false" 字符串均容忍)。

    error 非 None = 解析失败 / 结构不符 / disprove → caller fail-closed (passed=False)。
    真提取不到时把原始 stdout 摘要带进 error (Cap1c, 便于事后看 fork 到底输出了什么)。
    """
    candidates = _gather_candidates(stdout)
    # 取最后一个能定位到 verdict 的候选块 (fork 通常先散文/中间块, 最终 verdict 在最后)。
    for obj in reversed(candidates):
        found = _locate_verdict(obj, result_key, passed_key)
        if found is not None:
            verdict_obj, passed = found
            err = None if passed else f"fork verdict {passed_key}={verdict_obj.get(passed_key)!r} (未通过/disprove)"
            return verdict_obj, passed, err

    # 真提取不到 → fail-closed, 带原始 stdout 摘要便于事后看
    excerpt = _ANSI_RE.sub("", stdout).strip()
    excerpt = excerpt[:300] + ("…" if len(excerpt) > 300 else "")
    return {}, False, (
        f"fork verdict 缺 `{passed_key}` 键 (结构不符/无可解析 JSON) — fail-closed; "
        f"stdout 摘要: {excerpt!r}"
    )


# ────────────────────────────────────────────────────────────────────────────────
#  统一返回契约 (fork-unified-return-contract@v1) —— 六族 typed schema 一等化 + 全路径 fail-closed
#
#  概念 ground truth (fork-unified-return-contract@v1): fork 合回主线时的两层返回契约。
#    layer-1 纯 JSON 信封 (parse_fork_verdict 稳健抽取) + layer-2 按族 typed schema (本 registry)。
#  本 registry 是六族枚举的【代码一等化】, 被【全部派发路径】共用的单一收回真相源 ——
#  Agent-tool 手动/`--result-json` 回灌路径由 validate_return_contract 直接据它做 layer-2 结构
#  fail-closed; Python-spawn 路径由统一派发入口 dispatch_fork 对 gate 族按 result_key 注入该族
#  required_fields (f-fu03), 使 run_verification_fork 的 layer-2 (clause② validate_verdict_schema)
#  同样按族结构 fail-closed —— 两路径对同一 fork 输出判决 parity, 不是只给回灌路径读的平行结构
#  (test_fork_unified_return_contract.py 的 parity 测试 + test_fu08_migrate_gate_agg.py 的门禁族
#  layer-2 强制测试钉住)。注: consult 族 (advisor_verdict) 结构牙齿在回灌层的 class-aware 校验,
#  不经 spawn 路径的 passed-centric schema; verify_observer_verdict 等非六族的 fail-closed 在 spawn/
#  timeout 层, 均不在本 gate-族 spawn 注入的覆盖面内。
# ────────────────────────────────────────────────────────────────────────────────


class ReturnContractClass(StrEnum):
    """轴2 语义门控分类标签 (fork-unified-return-contract@v1 两条正交轴之轴2)。

    轴1 (结构一致性 fail-closed) 约束【全部三类 × 全部派发路径】, 无一豁免 (validate_return_contract
    对三类一视同仁做 layer-1+2)。轴2 (语义门控) 只 gate GATE 族。本枚举是【标签】—— caller 据它
    决定放不放行; gating 规则本体归 fork-orchestration-resilience@v1 条款③ null_vs_failclosed_rule
    (@ 引用, 本契约 USE 不 OWN), 故原语只返回标签、绝不在原语内 gate。
    """

    GATE = "gate"  # 门: 有效 passed=False → 不放行 (self_check_result / audit_verdict / verify_verdict)
    CONSULT = "consult"  # 咨询: passed 恒真、从不参与 gating (advisor_verdict); fail-closed 牙齿在 typed-field 层
    PRODUCER = "producer"  # 产提议: 主线收回去重/合并后用 (fork_result / findings_proposal)


@dataclass(frozen=True)
class FamilyReturnContract:
    """一族的返回契约元数据 (layer-2 该族 typed schema: result_key + 判决键 + 信封级必需字段)。"""

    result_key: str  # 该族信封唯一顶层键
    result_class: ReturnContractClass  # 轴2 分类标签
    passed_key: str  # 该族判决键 (轴2 gating 用; consult 族恒真)
    required_fields: tuple[str, ...]  # layer-2 信封级必需字段 (轴1 结构"牙齿"; 只校验存在性)


# 六族 typed schema 枚举 (概念 ground truth, Q1 收敛: advisor_verdict 归咨询-族)。required_fields =
# 该族 fail-closed 结构"牙齿"的【存在性】校验 —— 各族 proposal/verdict 内部逐字段【语义】(advisor
# decision 枚举/非空、fork_result proposal 的 pydantic、findings 的 voi_rationale …) 归各 caller /
# 各族 skill 的 pydantic 模型, 非本元契约 (概念: "本契约管的是元契约, 非各族逐字段清单")。
SIX_FAMILY_CONTRACTS: dict[str, FamilyReturnContract] = {
    "fork_result": FamilyReturnContract(
        "fork_result", ReturnContractClass.PRODUCER, "produced", ("produced", "proposal", "summary"),
    ),
    "self_check_result": FamilyReturnContract(
        "self_check_result", ReturnContractClass.GATE, "passed", ("passed", "blocking_checks"),
    ),
    "advisor_verdict": FamilyReturnContract(
        "advisor_verdict", ReturnContractClass.CONSULT, "passed",
        ("decision", "rationale", "evidence_scope_summary"),
    ),
    "audit_verdict": FamilyReturnContract(
        "audit_verdict", ReturnContractClass.GATE, "passed", ("verdict", "passed"),
    ),
    "findings_proposal": FamilyReturnContract(
        "findings_proposal", ReturnContractClass.PRODUCER, "completed", ("completed", "findings"),
    ),
    "verify_verdict": FamilyReturnContract(
        "verify_verdict", ReturnContractClass.GATE, "passed",
        ("passed", "verification_state", "falsification_attempt"),
    ),
}


@dataclass(frozen=True)
class ReturnContractOutcome:
    """统一返回契约收回结果 (validate_return_contract 产出)。

    accepted=False = 轴1 结构一致性 fail-closed 【拒收】(result_key 不在六族 / layer-1 抽不出信封 /
    layer-2 该族必需字段缺)。caller 拿到 accepted=False 绝不当有效返回信任/合入; 对 GATE 族
    additionally 不放行 (轴2, caller 据 result_class 决定)。accepted=True 时 passed = 该族判决键的
    coerce 值 (GATE 族 caller 据此 gating; PRODUCER 族 produced/completed=False 表示"没产出", CONSULT
    族恒真)。
    """

    accepted: bool
    result_key: str
    result_class: ReturnContractClass | None  # result_key 不在六族 → None (无从分类)
    envelope: dict[str, object]
    passed: bool
    error: str | None


def validate_return_contract(
    raw: str | dict[str, object],
    *,
    result_key: str,
) -> ReturnContractOutcome:
    """统一返回契约收回校验 —— fork-unified-return-contract@v1 全路径 fail-closed 收口 (轴1 结构)。

    覆盖【全部派发路径】(Python-spawn + Agent-tool 手动/`--result-json` 回灌) 的单一收回原语,
    闭合 debt-4f75b6a54f71 (手动路径此前【无代码级 fail-closed 收回】的主豁口):

      Layer-1 纯 JSON 信封: raw 是 str → 复用 parse_fork_verdict 稳健抽取 (容忍 fence/散文/多块/多包
        一层, 与 Python-spawn 路径【同宽容度】—— 手动路径绝不比 spawn 更严, 否则破坏全路径对称);
        真抽不出信封对象 (非纯 JSON / 无可解析对象 / 缺判决键) → 拒收。raw 已是 dict → 同口径定位。
      Layer-2 按族 typed schema: result_key 必须在六族枚举 (SIX_FAMILY_CONTRACTS —— 不在=拒收);
        该族信封级必需字段全在 + 判决键可 coerce (复用 validate_verdict_schema)。任一缺 → 拒收。

    轴2 语义门控【不在本原语】: 只返回 result_class + passed 让 caller 决定放不放行 (gating 归条款③
    null_vs_failclosed_rule, 本契约 USE 不 OWN)。

    fail-closed 是【拒收】不是告警: accepted=False + error 非 None。
    """
    contract = SIX_FAMILY_CONTRACTS.get(result_key)
    if contract is None:
        return ReturnContractOutcome(
            accepted=False,
            result_key=result_key,
            result_class=None,
            envelope={},
            passed=False,
            error=(
                f"result_key {result_key!r} 不在六族枚举 {sorted(SIX_FAMILY_CONTRACTS)} — "
                "拒收 (结构不符两层契约: layer-2 无对应族 typed schema)"
            ),
        )

    # Layer-1: 抽出信封对象 (str 走 parse_fork_verdict 稳健抽取; dict 走同口径 _locate_verdict 定位)。
    if isinstance(raw, str):
        envelope, passed, parse_err = parse_fork_verdict(
            raw, result_key=result_key, passed_key=contract.passed_key,
        )
    else:
        located = _locate_verdict(raw, result_key, contract.passed_key)
        if located is None:
            envelope, passed, parse_err = {}, False, (
                f"dict 内定位不到 `{contract.passed_key}` 键 (结构不符) — 拒收"
            )
        else:
            envelope, passed = located
            parse_err = None if passed else (
                f"{result_key} verdict {contract.passed_key}={envelope.get(contract.passed_key)!r}"
            )

    if not envelope:
        # layer-1 抽不出信封 → fail-closed 拒收 (非纯 JSON / 无可解析对象 / 缺判决键)。
        return ReturnContractOutcome(
            accepted=False,
            result_key=result_key,
            result_class=contract.result_class,
            envelope={},
            passed=False,
            error=parse_err or f"layer-1 抽不出 `{result_key}` 信封对象 (非纯 JSON / 结构不符) — 拒收",
        )

    # Layer-2: 该族信封级必需字段全在 + 判决键可 coerce (validate_verdict_schema 只校存在性, 非逐字段语义)。
    schema_err = validate_verdict_schema(
        envelope, passed_key=contract.passed_key, required_result_fields=contract.required_fields,
    )
    if schema_err is not None:
        return ReturnContractOutcome(
            accepted=False,
            result_key=result_key,
            result_class=contract.result_class,
            envelope=envelope,
            passed=False,
            error=f"layer-2 {result_key} typed schema 不符: {schema_err} — 拒收",
        )

    return ReturnContractOutcome(
        accepted=True,
        result_key=result_key,
        result_class=contract.result_class,
        envelope=envelope,
        passed=passed,
        error=parse_err,  # accepted 但 passed=False (如 produced=false / gate disprove) 时带原因
    )


# ────────────────────────────────────────────────────────────────────────────────
#  Mode resolution
# ────────────────────────────────────────────────────────────────────────────────


def resolve_fork_mode(explicit: str | None = None) -> ForkMode:
    """模式解析: explicit 参数 > 环境变量 TOWOW_VERIFICATION_FORK_MODE > 默认 real.

    默认 real —— 一旦走到 run_verification_fork(caller 已决定要独立验), 就真起独立子会话;
    不在此层静默回退内联。是否走 fork(vs caller 自己的内联降级)由 caller 显式决定。
    """
    val = explicit or os.environ.get(_ENV_MODE) or ForkMode.REAL.value
    val = val.strip().lower()
    try:
        return ForkMode(val)
    except ValueError as exc:
        msg = f"未知 verification fork mode: {val!r} (real|replay|disabled)"
        raise ForkSpawnError(msg) from exc


def resolve_timeout_s(explicit: int | None = None) -> int:
    """timeout 解析 (RUN-042 Cap3): explicit 参数 > env TOWOW_VERIFICATION_FORK_TIMEOUT_S > 默认.

    默认 DEFAULT_TIMEOUT_S=900s 给复杂验证余量。env / 参数让 ops 不改代码就能调。
    非法 env (非正整数) → 静默回落默认 (不让坏配置变成 0/负 timeout 把 fork 立即掐死)。
    """
    if explicit is not None and explicit > 0:
        return explicit
    raw = os.environ.get(_ENV_TIMEOUT)
    if raw is not None:
        try:
            val = int(raw.strip())
        except ValueError:
            val = 0
        if val > 0:
            return val
    return DEFAULT_TIMEOUT_S


# fork-crash-checkpoint-resume@v1: 崩溃 resume 续跑载荷 (f-fu04 修复) ——
# resume 用一句 continuation nudge 取代重发完整原 capsule prompt。--resume <uuid> 恢复的会话里,
# 完整原任务 (人格 + artifacts + 结构化输出 schema 指令) 已在会话历史中; headless `-p <payload>` 是
# 往已恢复会话里【追加一条新消息】。若 payload 仍是完整原 prompt = 把整个任务当新一轮从头再灌
# (inv-crash-resume-before-rerun 的『不重做已完成工作』只剩 argv 形态层, 语义层被重灌抵消)。改发
# nudge 后, 重灌在【结构上不可能】—— payload 里根本没有原任务内容, 模型只能从历史断点续跑。
# nudge 显式再点名要最终 verdict JSON (schema 指令在恢复的历史里, 不必重发)。
_RESUME_CONTINUATION_NUDGE = (
    "继续你此前被中断的独立验证 fork：完整任务、人格与待验 artifacts 已在本会话历史中，"
    "不要从头重做已经完成的分析/检视——从中断处接着推进。完成后只输出最终的结构化 verdict "
    "JSON（第一个字符 { 最后一个字符 }），不要复述任务或重新开始。"
)


# ────────────────────────────────────────────────────────────────────────────────
#  Public entry: run a blocking independent verification fork
# ────────────────────────────────────────────────────────────────────────────────


def run_verification_fork(
    *,
    fork_skill_id: str,
    prompt: str,
    repo_dir: Path,
    result_key: str = "self_check_result",
    passed_key: str = "passed",
    model: str | None = None,
    timeout_s: int | None = None,
    allowed_tools: Sequence[str] = READ_ONLY_TOOLS,
    forbidden_tools: Sequence[str] = FORBIDDEN_TOOLS,
    runner: ForkRunner | None = None,
    mode: str | None = None,
    replay_file: Path | None = None,
    per_check_key: str = "blocking_checks",
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_on_timeout: bool = False,
    required_result_fields: Sequence[str] = (),
    journal: ForkJournal | None = None,
    token_budget: ForkTokenBudget | None = None,
    concurrency_cap: ForkConcurrencyCap | None = None,
    extra_spawn_args: Sequence[str] = (),
) -> ForkVerdict:
    """起一个阻塞的独立验证 fork, 阻塞读回结构化 verdict.

    Args:
        fork_skill_id: 装的 fork 人格 id (execution-self-check / fix-self-check / advisor-consult …)
        prompt: 完整 capsule prompt (人格 + artifacts + 结构化输出 schema 指令)
        repo_dir: fork 子进程 cwd (能 Read/Grep/Bash 检视 worktree artifacts)
        result_key / passed_key: verdict 解析键
        model: None → clause ④ model_for_fork(fork_skill_id) 按 fork 类型解析 (决策/审计 OPUS,
            机械 SONNET); 显式传 str → 调用方覆盖。
        timeout_s: None → resolve_timeout_s (env / 默认 900s, RUN-042 Cap3); 显式正整数则用之
        runner: 注入缝 (单元测试假 fork verdict); None = 内置 claude -p headless spawn (_default_runner)
        mode: real|replay|disabled (None → env → 默认 real)
        replay_file: replay 模式下读 verdict 的 JSON 文件 (None → env TOWOW_VERIFICATION_FORK_REPLAY_FILE)
        max_retries: 基础设施类失败 (非零退出 / spawn OSError) 的重试次数 (RUN-042 Cap2,
            默认 1)。**绝不 retry 真 disprove** (passed=False 是有效判决直接返回)。
        retry_on_timeout: 是否对超时也 retry (默认 False —— 重试超时只会再等一个 timeout,
            解药是更长默认 timeout 而非重试)。
        required_result_fields: clause ② schema 必需字段 —— 定位到的 verdict 缺这些字段 = schema
            失败 → 重试 (默认 () = 只要求 passed 可识别, 与现状一致零回归)。
        journal: clause ① 编排 journal —— 传则先查缓存 (同 prompt 前缀命中直接返回不重 spawn),
            完成后落 journal (重启续跑)。
        token_budget: clause ⑥ 累计 token 预算 —— 传则把本次真 spawn 的用量累加进去 (可查)。
        concurrency_cap: clause ⑤ fork 层并发帽 —— 传则真 spawn 在帽的 slot 内进行 (防 spawn 风暴)。

    Returns:
        ForkVerdict (passed 据 verdict; spawned 标是否真起子进程; retried 标是否 retry 过;
        from_journal 标是否缓存命中; usage 带本次 token 用量)。

    Raises:
        ForkSpawnError / ForkTimeoutError: 基础设施失败 (claude 不可用 / 超时 / 非零退出 /
            mode=disabled / replay 文件缺), retry 用尽后仍失败 —— fail-closed, caller 不得静默放行。
    """
    resolved_model = model if model is not None else model_for_fork(fork_skill_id)
    resolved_mode = resolve_fork_mode(mode)
    resolved_timeout = resolve_timeout_s(timeout_s)
    # ── fork-crash-checkpoint-resume@v1: 预分配 fork 自身 session-id ──────────────────────
    # 仅当 REAL 且 extra_spawn_args 里没有既有 `--resume` 时才预分配 (advisor 守卫): 展开族的
    # extra_spawn_args 已含 `--resume <parent> --fork-session` 继承父会话, 叠加 `--session-id` 是
    # 未在 headless 验证过的 CLI 三 flag 组合 → 不碰 (展开族崩溃续跑登债, 归 fork-context-mode)。
    # 这自然把崩溃续跑收敛到 gate 族 (零继承 `claude -p`), 正是 goal⑤ 要的 headless --resume 路径。
    fork_session_id: str | None = None
    if resolved_mode is ForkMode.REAL and "--resume" not in extra_spawn_args:
        fork_session_id = str(uuid.uuid4())

    def _argv_pair(sid: str | None) -> tuple[list[str], list[str]]:
        """构造某会话 id 的 (spawn_argv, resume_argv)。

        spawn_argv: `claude -p ... --session-id <sid>` (sid=None → 不带 session-id: 展开族已由
        extra_spawn_args 的 `--resume <parent>` 继承父会话 / BG_POLLED / 非 REAL, 无预分配)。
        resume_argv: 去掉 `--session-id` 换成 `--resume <sid>` (续同一 session, 看到崩溃前全部已
        持久化历史, 不重做已完成工作)。**不加 `--fork-session`** —— fork-session 会另开新 id, 那是
        展开族继承父会话的语义; 崩溃续跑要的是复用 fork 自身那个 session 接着推进。f-fu04: `-p` 载荷
        用 _RESUME_CONTINUATION_NUDGE 而非完整原 prompt —— 原任务已在恢复会话历史, 重发完整 prompt
        会把整个任务当新一轮从头再灌 (语义层重做), 发 nudge 则重灌在结构上不可能。sid=None → 无 resume 能力。

        f-fork-timeout-retry-sessionid-collision criterion 1: 从零 retry 用【新 sid】重建本对
        (不复用首次 spawn 已注册的 --session-id, 否则撞 claude CLI 'Session ID already in use');
        故本 helper 参数化 sid, spawn+resume 成对生成 —— resume 永远指向本次 attempt 自己的 session。
        """
        spawn = build_fork_command(
            prompt, model=resolved_model, allowed_tools=allowed_tools,
            forbidden_tools=forbidden_tools, session_id=sid, extra_args=extra_spawn_args,
        )
        resume: list[str] = []
        if sid is not None:
            resume = build_fork_command(
                _RESUME_CONTINUATION_NUDGE, model=resolved_model, allowed_tools=allowed_tools,
                forbidden_tools=forbidden_tools, extra_args=["--resume", sid],
            )
        return spawn, resume

    argv, resume_argv = _argv_pair(fork_session_id)

    # ── clause ① journal/resume: 同 (skill+prompt+model+keys) 缓存命中 → 不重 spawn, 不重花钱 ──
    cache_key = (
        fork_cache_key(
            fork_skill_id=fork_skill_id, prompt=prompt, model=resolved_model,
            result_key=result_key, passed_key=passed_key,
        )
        if journal is not None
        else None
    )
    if journal is not None and cache_key is not None:
        cached = journal.lookup(cache_key)
        if cached is not None:
            return cached  # 缓存命中: spawned=False from_journal=True; 不动 budget (无新 spend)

    def _finalize(
        verdict_obj: dict[str, object],
        passed: bool,
        err: str | None,
        *,
        stdout: str,
        spawned: bool,
        exit_code: int,
        cmd: str,
        retried: bool,
        resumed: bool = False,
        harvested_from_timeout: bool = False,
    ) -> ForkVerdict:
        """统一收尾 (clause ⑥ usage / clause ⑥ budget / clause ① journal record)."""
        usage = extract_fork_usage(stdout)
        verdict = ForkVerdict(
            passed=passed,
            result=verdict_obj,
            spawned=spawned,
            fork_skill_id=fork_skill_id,
            model=resolved_model,
            command_text=cmd,
            exit_code=exit_code,
            stdout_excerpt=stdout[:500],
            error=err,
            per_check=_coerce_per_check(verdict_obj.get(per_check_key)),
            retried=retried,
            resumed=resumed,
            harvested_from_timeout=harvested_from_timeout,
            usage=usage,
        )
        if token_budget is not None and spawned:
            token_budget.add(usage)  # 只计真 spawn 的 spend (replay/缓存不算新花)
        # clause ①: 只缓存【产出了有效 verdict 的完成】(verdict_obj 非空), 不缓存瞬态失败。
        if journal is not None and cache_key is not None and verdict_obj:
            journal.record(cache_key, verdict)
        return verdict

    if resolved_mode is ForkMode.DISABLED:
        msg = (
            f"verification fork disabled (mode=disabled) for {fork_skill_id} — "
            "caller 须显式降级 + 记账, 不静默放行"
        )
        raise ForkSpawnError(msg)

    if resolved_mode is ForkMode.REPLAY:
        rf = replay_file or (
            Path(os.environ[_ENV_REPLAY_FILE]) if _ENV_REPLAY_FILE in os.environ else None
        )
        if rf is None or not rf.is_file():
            msg = f"replay mode 缺 verdict 文件 (replay_file / {_ENV_REPLAY_FILE}): {rf}"
            raise ForkSpawnError(msg)
        stdout = rf.read_text(encoding="utf-8")
        verdict_obj, passed, err = parse_fork_verdict(
            stdout, result_key=result_key, passed_key=passed_key,
        )
        return _finalize(
            verdict_obj, passed, err, stdout=stdout, spawned=False,
            exit_code=0, cmd=f"<replay {rf}>", retried=False,
        )

    # ── BG_POLLED (R07/R12 非 headless): 必须注入 bg-polled runner (drop-file + spawn) ──
    if resolved_mode is ForkMode.BG_POLLED and runner is None:
        msg = (
            f"bg_polled mode 需注入 bg-polled runner (drop-file + spawn) for {fork_skill_id} — "
            "用 bg_polled_runner_factory 造; fail-closed (不静默回退)"
        )
        raise ForkSpawnError(msg)

    # ── REAL: 真起 claude -p headless 独立子会话 (built-in _default_runner) ──
    run = runner or _default_runner
    # 可用性检查只对内置 spawn (REAL 且未注入 runner); 注入 runner 时跳过 (测试假进程 / caller 自带 spawn)。
    if resolved_mode is ForkMode.REAL and runner is None and not claude_headless_available():
        msg = (
            f"claude headless (`claude -p`) 不可用 — 无法起独立验证 fork {fork_skill_id}; "
            "fail-closed (不静默回退内联自评假装独立)"
        )
        raise ForkSpawnError(msg)

    def _spawn_loop() -> ForkVerdict:
        # 崩溃处理 (RUN-042 Cap2/Cap2b + fork-crash-checkpoint-resume@v1)。崩溃三分:
        #   ① 基础设施失败 (spawn OSError / 非零退出) + ② 超时 → **先从 fork 会话存档断点 resume 续跑**
        #      (有预分配 session-id 且本轮未 resume 过时, 半成品不作废); resume 不可行 (无 session-id:
        #      展开族/BG_POLLED, 或已 resume 过) 才落回【从零 retry】(DEFAULT_MAX_RETRIES=1 fallback 上限)。
        #   ③ 真判决 (passed=False 等有效 disprove) → **既不 resume 也不 retry** (R12 底线: 见下方
        #      schema 合法即直接 return; 重跑有效判决 = 自欺)。
        #   returncode==0 但 verdict 残缺 (空 result / 截断 / 不可解析) = 进程正常结束、只是输出坏,
        #      **不是崩溃** → 走从零 schema-retry (不 resume; 缩小改动面)。
        # resume 独立于 attempt/max_retries: resume 是"续半成品"(不重花整支), 从零 retry 是 fallback。
        # fork_session_id is None 时 (展开族/BG_POLLED/非 REAL) resume 分支全跳过 → 行为与现状完全一致。
        attempt = 0
        resumed = False
        current_session_id = fork_session_id
        current_argv = argv
        current_resume_argv = resume_argv

        def _fresh_retry_argv() -> list[str]:
            """从零 retry 的 spawn argv (f-fork-timeout-retry-sessionid-collision criterion 1)。

            有预分配 session-id 时【换新 uuid】重建 spawn+resume 对: 首次 spawn 已向 claude CLI 注册了
            原 --session-id, 从零重试再用它必撞 'Session ID already in use' 且遮蔽真实错误 —— 换新 sid
            让重试要么干净续跑要么以真实错误失败。无预分配 (current_session_id is None: 展开族/BG_POLLED/
            非 REAL) → 原样返回, 无 session-id 无碰撞, 行为与现状一致。current_resume_argv 一并跟着新
            sid 走 —— 若这次重试自身再崩需 resume, resume 指向的是本次 attempt 自己的 session (非旧 X)。
            """
            nonlocal current_session_id, current_resume_argv
            if current_session_id is None:
                return current_argv
            current_session_id = str(uuid.uuid4())
            spawn, resume = _argv_pair(current_session_id)
            current_resume_argv = resume
            return spawn

        def _harvest_timeout_verdict(exc: ForkTimeoutError) -> ForkVerdict | None:
            """criterion 2: 超时异常携带的 partial_stdout 含【合法】verdict → 收割为完成的 ForkVerdict;
            否则 (空 / 截断 / 非法 / schema 不合法) → None, 落回 resume/retry。

            fail-closed 完整性 (advisor ①): fork 被杀在写 verdict 中途 → stdout 是半截 JSON, 绝不能
            凑出个 verdict 碎片就放行 (那是亲手造 hollow closure)。故必须过 parse + validate_verdict_schema
            双关, 任一不过即不收割。收割成功的 verdict 标 harvested_from_timeout=True (provenance 诚实,
            非干净退出), passed 据实际 verdict —— 不因收割而翻转判决。
            """
            partial = getattr(exc, "partial_stdout", "") or ""
            if not partial.strip():
                return None
            verdict_obj, passed, err = parse_fork_verdict(
                partial, result_key=result_key, passed_key=passed_key,
            )
            if not verdict_obj:
                return None
            schema_err = validate_verdict_schema(
                verdict_obj, passed_key=passed_key,
                required_result_fields=required_result_fields,
            )
            if schema_err is not None:
                return None  # 截断/非法 verdict 绝不收割 —— 落回 resume/retry, 保 fail-closed
            return _finalize(
                verdict_obj, passed, err, stdout=partial, spawned=True,
                exit_code=0, cmd=command_text(current_argv),
                retried=attempt > 0, resumed=resumed, harvested_from_timeout=True,
            )

        while True:
            try:
                res = run(current_argv, repo_dir, resolved_timeout)
            except ForkTimeoutError as timeout_exc:
                # criterion 2: 先收割超时瞬间已产出的合法 verdict (已完成的工作不因收割时序被判失败)。
                harvested = _harvest_timeout_verdict(timeout_exc)
                if harvested is not None:
                    return harvested
                # ②: 超时升级为可 resume (半成品不作废), 不受 retry_on_timeout 门控 —— 那个 flag 只管
                # 【从零重跑】超时 (再等一个 timeout 病态), resume 是续半成品、另一回事。
                if current_session_id is not None and not resumed:
                    resumed = True
                    current_argv = current_resume_argv
                    continue
                # resume 不可行 → 沿用原语义: 超时默认不从零 retry (retry_on_timeout 控制)。
                if retry_on_timeout and attempt < max_retries:
                    attempt += 1
                    current_argv = _fresh_retry_argv()
                    continue
                raise
            except ForkMemoryAdmissionError:
                # 口子4 补防线: 内存门已在 _default_runner 内做过有界延迟才拒, resume/retry 只会再叠
                # 一次延迟、白占会话内存, 内存也不会在一个周期内可靠回落 → 短路不重试上抛
                # (门 fork fail-closed 不放行, 由 orchestrator 内存回落后整条任务重派)。
                raise
            except ForkSpawnError:
                # ①: spawn 失败 (OSError / FileNotFound 等基础设施类) → 先 resume, 不可行才从零 retry。
                if current_session_id is not None and not resumed:
                    resumed = True
                    current_argv = current_resume_argv
                    continue
                if attempt < max_retries:
                    attempt += 1
                    current_argv = _fresh_retry_argv()
                    continue
                raise
            if res.returncode != 0:
                # ①: 非零退出 = 基础设施类崩溃 → 先 resume, 不可行才从零 retry。
                if current_session_id is not None and not resumed:
                    resumed = True
                    current_argv = current_resume_argv
                    continue
                if attempt < max_retries:
                    attempt += 1
                    current_argv = _fresh_retry_argv()
                    continue
                msg = (
                    f"verification fork {fork_skill_id} 非零退出 (exit={res.returncode}): "
                    f"{res.stderr[:300]}"
                )
                raise ForkSpawnError(msg)

            verdict_obj, passed, err = parse_fork_verdict(
                res.stdout, result_key=result_key, passed_key=passed_key,
            )
            # clause ②: schema 校验 (passed 可识别 + 必需字段齐)。失败 = 结构残缺 (returncode==0 但
            # 输出坏, 非崩溃) → 从零 schema-retry (不 resume); passed=False 是合法判决 → 不重试直接返回。
            schema_err = (
                validate_verdict_schema(
                    verdict_obj, passed_key=passed_key,
                    required_result_fields=required_result_fields,
                )
                if verdict_obj
                else "fork 未产出可解析 verdict (空)"
            )
            if schema_err is not None and attempt < max_retries:
                attempt += 1
                current_argv = _fresh_retry_argv()  # schema-retry 从零 (换新 sid, 不 resume)
                continue
            if schema_err is not None:
                # 重试用尽仍 schema 不合法 → fail-closed (不放行未经合法 verdict 的判断)。
                passed = False
                err = err or schema_err
            return _finalize(
                verdict_obj, passed, err, stdout=res.stdout, spawned=True,
                exit_code=res.returncode, cmd=command_text(current_argv),
                retried=attempt > 0, resumed=resumed,
            )

    # clause ⑤: 真 spawn 在并发帽 slot 内进行 (传了帽才限; 不传零回归)。
    if concurrency_cap is not None:
        with concurrency_cap.slot():
            return _spawn_loop()
    return _spawn_loop()


def _coerce_per_check(raw: object) -> list[dict[str, object]]:
    """verdict 里的 per-check 列表 (容错: 非 list → 空)."""
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


# ────────────────────────────────────────────────────────────────────────────────
#  统一 fork 派发入口 (fork-unified-dispatch-entry@v1) —— 收敛两锚点为按族路由的单入口
# ────────────────────────────────────────────────────────────────────────────────


class ForkFamily(StrEnum):
    """fork 两族 (fork-context-mode-by-family@v1 心脏分类; 有且仅有两值, 无第三态).

    - EXPAND (展开族): 产出=研究/分析/结构化提案, 正确工作需父对话完整语境 → 继承主会话上下文。
      入口 spawn 形态 = `claude -p --resume <parent-session-id> --fork-session`。
    - GATE (把关族): 产出=裁决/判定 (pass-fail / finding 真伪 / 放行与否), 价值依赖『看不到出活方
      思路』的独立性 → 零继承 + capsule 投喂。入口 spawn 形态 = `claude -p` (现状零继承)。
    """

    EXPAND = "expand"
    GATE = "gate"


# fork_skill_id → ForkFamily 现役归族 (ground truth: fork-context-mode-by-family@v1 归族清单)。
# 全仓 fork 调用点清点表 (T-FU-06) 是权威登记; 本表是入口路由的落地映射, 覆盖 dispatch-entry
# 概念枚举的全部场景 (M-1.2 共识 6 / M-1.4 self-check+advisor / M-1.6 fix-self-check / M-0.5
# audit / M-1.5 review 聚合 / 采访 5)。判族判据: 裁决且依赖独立性 → gate; 研究/分析/提案且需父
# 语境 → expand (按产出性质+独立性依赖, 不按 skill 名字)。
_FORK_FAMILY_MAP: dict[str, ForkFamily] = {
    # ── GATE (把关族: 裁决, 独立性红线, 零继承) ──
    "execution-self-check": ForkFamily.GATE,  # M-1.4 self-check 门
    "fix-self-check": ForkFamily.GATE,  # M-1.6 self-check 门
    "audit": ForkFamily.GATE,  # M-0.5 审计门
    "closure-audit": ForkFamily.GATE,  # M-0.5 关闭证据审计门
    "verify-step": ForkFamily.GATE,  # F-08d 独立 verify (disprove)
    "method-execution-path": ForkFamily.GATE,  # F-08c 执行路径 lens (falsification)
    "method-consistency": ForkFamily.GATE,  # F-08c 一致性 lens
    "method-red-team": ForkFamily.GATE,  # F-08c 红队 lens
    "meta-review": ForkFamily.GATE,  # F-08g 元 review (审 review_plan 够不够 = 裁决)
    "engineering-consistency-verify": ForkFamily.GATE,  # 共识冻结前终检 (门; 判据异于采访同名)
    "verify-observer": ForkFamily.GATE,  # R07 观测抽查 (BG_POLLED 遗留, 仍属把关)
    # ── EXPAND (展开族: 研究/分析/提案, 需父语境, 继承) ──
    "advisor-consult": ForkFamily.EXPAND,  # M-1.4 决策者 (需主 session 判断困难语境)
    "engineering-prep-research": ForkFamily.EXPAND,  # M-1.2 共识生成类 (consistency-verify 除外)
    "concept-definition": ForkFamily.EXPAND,
    "state-machine-define": ForkFamily.EXPAND,
    "consumer-scan": ForkFamily.EXPAND,
    "invariant-extract": ForkFamily.EXPAND,
    "interview-prep-research": ForkFamily.EXPAND,  # 采访 5 fork (迁回统一机制; 分析需对话语境)
    "interview-conops-construct": ForkFamily.EXPAND,
    "interview-influence-diagram": ForkFamily.EXPAND,
    "interview-sdm-reverse": ForkFamily.EXPAND,
    "interview-consistency-verify": ForkFamily.EXPAND,
    "plan-decompose": ForkFamily.EXPAND,  # planning fork (分解/依赖/关键路径/跨计划 = 分析提案)
    "dependency-analyze": ForkFamily.EXPAND,
    "critical-path-schedule": ForkFamily.EXPAND,
    "cross-plan-check": ForkFamily.EXPAND,
    "review-aggregation": ForkFamily.EXPAND,  # M-1.5 review 聚合 (聚合 findings = 生成)
    "review-plan-creator": ForkFamily.EXPAND,  # M-1.5 review_plan 设计 (生成)
}


def fork_family(fork_skill_id: str) -> ForkFamily:
    """判一个 fork 属哪族 (fork-context-mode-by-family@v1)。

    未登记的 fork → 默认 GATE (保守 fail-safe: 独立性是把关族存在意义, 宁可给独立零继承也不误把
    父上下文喂给一个可能是门的 fork —— 破 gate 族独立性是红线, 反向不是)。新 fork 应显式登记进
    _FORK_FAMILY_MAP (并同步全仓清点表 T-FU-06)。
    """
    return _FORK_FAMILY_MAP.get(fork_skill_id, ForkFamily.GATE)


def build_expand_spawn_args(parent_session_id: str) -> list[str]:
    """展开族的上下文继承 spawn 参数 (fork-unified-dispatch-entry@v1 spawn 形态钉死)。

    `--resume <parent-session-id> --fork-session`: 分叉子进程在 `-p` 之上完整继承主对话历史
    (--fork-session 复制完整历史至新 session, 原会话不变)。入口只钉『按族给这个 flag』; 精确继承
    机制/生效验证归 fork-context-mode-by-family@v1。
    """
    return ["--resume", parent_session_id, "--fork-session"]


def is_resumable_claude_session_uuid(parent_session_id: str | None) -> TypeGuard[str]:
    """parent_session_id 能否喂 `claude --resume`？须是合法 Claude 会话 UUID (canonical 形态)。

    debt-0529dd0a268c: 展开族拼 `--resume <parent> --fork-session`, 但 `claude --resume` 只认
    Claude 会话 UUID/标题; towow 会话 id (sess-interview-* / sess-work-* / 12hex) 是与 Claude 会话
    id 无映射的另一套体系, 喂进去 claude 直接 `Provided value "..." is not a UUID` 退出 —— 采访 5
    fork 的默认继承路径因此在任何环境从未真正工作过, 每次靠人识别报错手动 inline 降级。

    入口 (dispatch_fork) 在拼 resume 前用本判据把关: 非合法 Claude UUID → 视同无父会话, 走与
    `parent_session_id=None` 完全相同的诚实降级 (extra_spawn_args=[] 零继承 + capsule 投喂, 同
    consensus fork-consult 已实证可用口径), 不把非 UUID 拼进 spawn 炸掉。

    canonical 判据 = `str(uuid.UUID(x)) == x.lower()`: 只认带连字符的标准 UUID 形态 (`claude --resume`
    真正接受的形态); 32-hex 无连字符 / 12-hex towow id 等一律 False。
    """
    if not parent_session_id:
        return False
    try:
        return str(uuid.UUID(parent_session_id)) == parent_session_id.lower()
    except (ValueError, AttributeError):
        return False


def dispatch_fork(
    *,
    fork_skill_id: str,
    prompt: str,
    repo_dir: Path,
    parent_session_id: str | None = None,
    family: ForkFamily | None = None,
    **run_kwargs: object,
) -> ForkVerdict:
    """统一 fork 派发入口 (fork-unified-dispatch-entry@v1) —— 任何 agent 派 fork 的唯一合规路径。

    收敛两锚点 (consensus fork-consult CLI + verification_fork.run_verification_fork) 为按族路由
    的单入口: 判 fork 属 expand/gate 族 → 按族选 spawn 形态 → 委托 run_verification_fork 真派发。

    路由 (REAL 默认 headless `claude -p`, build_fork_command 现状形态保持):
      - GATE 族: 零继承 (extra_spawn_args=[]) —— 全新空白 context + capsule 投喂, 独立性红线。
      - EXPAND 族: parent_session_id 是合法 Claude 会话 UUID → 加 `--resume <parent> --fork-session`
        继承父上下文; None **或非 Claude UUID** (如 towow sess-interview-* 会话 id) → 诚实降级零继承
        (extra_spawn_args=[], 同 consensus fork-consult 已实证口径), 不静默假装继承、也不把非 UUID 拼进
        spawn 炸掉 (debt-0529dd0a268c: `claude --resume` 只认 Claude UUID, towow 会话 id 喂进去必崩)。
        安全网: 形似 UUID 却仍不可 resume (如当前 bg agent 自己的会话, CLI 拒 --resume) → 带 --resume
        的 spawn 失败后【退零继承重试一次】跑通; 真基础设施 down 连零继承也失败则上抛 fail-closed。
        任何降级都在返回的 ForkVerdict.expand_degraded 留因 (非静默, caller 据此记账/告知)。

    Args:
        fork_skill_id: fork 人格 id, 决定归族 (若 family 未显式传)。
        parent_session_id: expand 族继承的父会话 id (gate 族忽略)。
        family: 显式覆盖归族 (None → fork_family(fork_skill_id) 判)。
        **run_kwargs: 透传给 run_verification_fork (result_key/passed_key/model/runner/mode/...)。

    Returns:
        ForkVerdict (同 run_verification_fork)。
    """
    fam = family if family is not None else fork_family(fork_skill_id)
    extra_spawn_args: list[str] = []
    expand_degraded: str | None = None
    # debt-0529dd0a268c: 只有合法 Claude 会话 UUID 才拼 --resume; 非 UUID (towow sess-* 会话 id)
    # 视同无父会话, 落零继承降级 (与 parent_session_id=None 同路径), 不再把 towow 会话 id 拼进
    # `claude --resume` 炸掉 spawn。降级带 provenance (非静默): parent 给了却用不上时如实记因 ——
    # 只在【给了 parent 但用不上】时记 (parent=None 是本就无继承, 不算降级)。
    if fam is ForkFamily.EXPAND and parent_session_id is not None:
        if is_resumable_claude_session_uuid(parent_session_id):
            extra_spawn_args = build_expand_spawn_args(parent_session_id)
        else:
            expand_degraded = (
                f"expand 族继承降级(零继承): parent_session_id={parent_session_id!r} 非可 resume 的 "
                "Claude 会话 UUID (towow 会话 id 与 Claude 会话 id 无映射) → 靠 capsule/prompt 自足跑, "
                "不把非 UUID 拼进 --resume 炸 spawn"
            )
    # ── fork-unified-return-contract@v1 layer-2 真落到 Python-spawn 路径 (f-fu03 修复) ──
    # gate 族经统一入口派发时, 按 result_key 从 SIX_FAMILY_CONTRACTS 注入该族 required_fields ——
    # 使 run_verification_fork 的 layer-2 (clause② validate_verdict_schema) 真按族结构 fail-closed:
    # gate fork 返回缺该族必需字段 (如 verify_verdict 缺 verification_state/falsification_attempt) 被
    # 拒收 → passed=False 不放行, 与 validate_return_contract 回灌路径 parity。单一真相源 (registry)
    # 真落到 spawn 路径, 不再只有回灌路径读它 (此前 gate 路径 layer-2 只校 passed 可 coerce)。
    #   - 只 gate 族注入 (ForkFamily.GATE): CONSULT 族 (advisor_verdict) passed 恒真、结构牙齿在
    #     validate_return_contract 的 class-aware 回灌层, 不走本 passed-centric 的 validate_verdict_schema
    #     → 不注入 (注入会把 EXPAND/consult 的 required 强加到 spawn 路径, 破其语义)。PRODUCER-as-gate
    #     (findings_proposal 归 ForkFamily.GATE) 一并注入 (它 routes gate)。
    #   - caller 已显式传 required_result_fields → 不覆盖 (尊重显式意图, 如两 EXPAND fork_result caller)。
    #   - result_key 不在六族 (如 verify_observer_verdict, bg_polled 特例) → 查不到 contract → 不注入
    #     (no-op, 零回归); 其结构 fail-closed 在 spawn/timeout 层。
    if fam is ForkFamily.GATE and "required_result_fields" not in run_kwargs:
        gate_result_key = run_kwargs.get("result_key", "self_check_result")
        contract = (
            SIX_FAMILY_CONTRACTS.get(gate_result_key)
            if isinstance(gate_result_key, str)
            else None
        )
        if contract is not None:
            run_kwargs["required_result_fields"] = contract.required_fields

    def _run(spawn_args: Sequence[str]) -> ForkVerdict:
        return run_verification_fork(
            fork_skill_id=fork_skill_id,
            prompt=prompt,
            repo_dir=repo_dir,
            extra_spawn_args=spawn_args,
            **run_kwargs,  # type: ignore[arg-type]
        )

    # debt-0529dd0a268c 安全网 (pre-check 的兜底): 带 --resume 的 spawn 失败 → 退零继承重试一次。
    # pre-check (is_resumable_claude_session_uuid) 短路了【结构上就非 UUID】的多数 towow 会话 id;
    # 但 "形似 UUID 却仍不可 resume" 的残余仍会漏过去 (最典型: 当前 bg agent 自己的会话 —— 有合法
    # UUID 但 `claude --resume <它>` 被 CLI 拒, 见 l2.claude_bg_helper "currently running as a
    # background agent")。那类失败是 resume 专属, 退掉 --resume 零继承重试即可跑通; 真基础设施 down
    # 则连零继承也失败 → 上抛 fail-closed。这层把"修好这个 id"升级成"修好这类" (类别级 never-again)。
    try:
        verdict = _run(extra_spawn_args)
    except ForkSpawnError:
        if not extra_spawn_args:
            raise  # 本就零继承 (gate 族 / 无 parent / pre-check 已降级) → 真基础设施 down, fail-closed
        expand_degraded = (
            f"expand 族继承降级(零继承): 带 --resume {parent_session_id!r} 的 spawn 失败 "
            "(parent 形似 UUID 但 claude 拒 resume), 退零继承重试一次"
        )
        verdict = _run([])  # 退零继承重试; 再失败则本次不 catch → 上抛 fail-closed
    # provenance 诚实: 降级过就盖到 verdict 上 (caller 据此记账/告知; frozen dataclass 用 replace)。
    return replace(verdict, expand_degraded=expand_degraded) if expand_degraded is not None else verdict


# ────────────────────────────────────────────────────────────────────────────────
#  clause ③ failure-policy 包装: 门 fail-closed / 咨询 null-tolerable
# ────────────────────────────────────────────────────────────────────────────────


def run_fork_with_failure_policy(
    *,
    fork_skill_id: str,
    prompt: str,
    repo_dir: Path,
    policy: ForkFailurePolicy | None = None,
    **kwargs: object,
) -> ForkVerdict | None:
    """clause ③ 按 fork 类别决定失败语义的包装。

    透传 run_verification_fork 的全部参数 (**kwargs)。区别只在【单 fork 死/超时怎么办】:

    - **门 fork** (policy=GATE_FAIL_CLOSED, 默认按 fork_failure_policy 判): ForkSpawnError /
      ForkTimeoutError 直接上抛 —— 对应的门 fail-closed 不放行 (与 anti-fake-done-gate-fail-closed
      同口径)。这跟直接调 run_verification_fork 行为一致。
    - **咨询/研究 fork** (policy=CONSULT_NULL_TOLERABLE): 失败返回 **None** —— 该位结果 = null,
      调用方显式处理 (不能假装拿到了结果, 但编排不崩, 可重 consult / 跳过 / 升级)。

    policy=None → fork_failure_policy(fork_skill_id) 自动判别 (未登记 fork 保守当门 fail-closed)。

    注意: 这只改【基础设施失败】的语义。fork 真跑出 verdict (含 passed=False disprove) 一律原样
    返回 ForkVerdict —— disprove 是有效判决, 不是失败, 永远不会被这层吞成 null。
    """
    resolved_policy = policy if policy is not None else fork_failure_policy(fork_skill_id)
    try:
        return run_verification_fork(
            fork_skill_id=fork_skill_id, prompt=prompt, repo_dir=repo_dir, **kwargs,  # type: ignore[arg-type]
        )
    except ForkSpawnError:
        if resolved_policy is ForkFailurePolicy.GATE_FAIL_CLOSED:
            raise  # 门: fail-closed, 不放行
        return None  # 咨询/研究: null 可容忍, 不炸编排


__all__ = [
    "DEFAULT_FORK_CONCURRENCY_CAP",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "FORBIDDEN_TOOLS",
    "MODEL_SONNET",
    "READ_ONLY_TOOLS",
    "SIX_FAMILY_CONTRACTS",
    "FamilyReturnContract",
    "ForkConcurrencyCap",
    "ForkFailurePolicy",
    "ForkFamily",
    "ForkJournal",
    "ForkMemoryAdmissionError",
    "ForkMode",
    "ForkRunResult",
    "ForkRunner",
    "ForkSpawnError",
    "ForkTimeoutError",
    "ForkTokenBudget",
    "ForkUsage",
    "ForkVerdict",
    "ReturnContractClass",
    "ReturnContractOutcome",
    "bg_polled_runner_factory",
    "build_expand_spawn_args",
    "build_fork_command",
    "claude_headless_available",
    "command_text",
    "dispatch_fork",
    "drop_verdict_instruction",
    "extract_fork_usage",
    "fork_cache_key",
    "fork_failure_policy",
    "fork_family",
    "fork_verdict_dir",
    "model_for_fork",
    "new_collision_proof_drop_file",
    "parse_fork_verdict",
    "poll_verdict_dropfile",
    "resolve_fork_concurrency_cap",
    "resolve_fork_mode",
    "resolve_timeout_s",
    "run_fork_with_failure_policy",
    "run_verification_fork",
    "validate_return_contract",
    "validate_verdict_schema",
]
