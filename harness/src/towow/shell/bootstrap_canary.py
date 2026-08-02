"""T-RMD-S5-CANARY (段⑤) — bootstrap canary + 部署后健康检查 + 起不来回 LKG.

# spec source:
#   01-reconciliation/SELF-DRIVE-SUBSTRATE-DESIGN.md §4.3 bootstrap 保护
#   concept: rc-layer-container-controlled-promotion ([层④]容器+受控放行+部署+bootstrap)
#
# 根治 finding f-sub-bootstrap-canary-missing: 自更新砖机保护 canary/健康检查/回滚不存在.
#
# 问题 (§4.3): harness 修自己 → 部署(worktree auto-promote 回 main) → 下次自修复用新代码.
# **危险: 坏的自我更新可能弄坏"自我更新能力本身"(把自己砖了)**. 一个 import 就崩 / 派发核心被
# 改坏的 commit 一旦 promote 进 main, 下次 daemon 重启起不来 → autopilot 死 → 再也修不了自己.
#
# 三道保护 (§4.3 ①②③):
#   ② 碰 deploy/heartbeat/dispatch 核心的改动, **先 canary** —— 一个测试 harness 实例跑新代码,
#      验它自己的"自部署+心跳+派发"还活, 过了才 promote (`canary_gate`, 挂在 orchestrator
#      auto-promote 前). 不活 → 不 promote, main 停在 LKG (held).
#   ① 自我部署后健康检查, 起不来自动回 last-known-good (`health_gate` + `rollback_to_lkg`,
#      daemon (re)start 前跑; 起不来 → code-only restore src 到 LKG, 绝不碰 .towow 账本).
#   LKG (last-known-good) 追踪: 每次 smoke 过的 main commit 记成 LKG (`record_lkg`).
#
# ★ 一个机制两处挂点 (advisor 定): canary 与 health 共用同一个 `run_smoke` —— "候选代码能不能
#   import + 心跳一拍 + 派发算一次, 不崩". pre-promote 调它=canary, post-restart 调它=health.
#
# ★ 默认 off, flag-off 路径保证 no-op (镜像 TOWOW_EXEC_ISOLATION): daemon 是 live real-spawn,
#   下次重启吃我这段新代码; fail-closed 的 canary 门若默认 on, canary infra 任何 bug 都会挡死
#   *所有* 碰核心的 auto-promote (含本 plan 自己) = 用自己的 task 把 autopilot 砖了. 故 enforcement
#   gated on owner ignition (TOWOW_BOOTSTRAP_CANARY=on), 与段⑥受控点亮同闸.
#
# ★ rollback 参数化到 target_repo, 绝不自动回滚 live 共享主树: main 上同时驻留 tracked 账本
#   (.towow/), git 回滚 main 会连账本一起回退 (2026-06-10 双事故 / B4-07 物理门拦那一类). 故
#   canary-fail = 不 promote (main 本就停在 LKG, held, 零 git 动作); 真正 code-only restore 只
#   对参数传入的 target_repo 跑 (测试里=throwaway repo; 生产里=gated 恢复手术), 永不自动碰共享树.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from towow.l0.event_log import EventLog

# ════════════════════════════════════════════════════════════════════════════════
#  Enforcement flag (default off — flag-off path is a guaranteed no-op)
# ════════════════════════════════════════════════════════════════════════════════

_CANARY_FLAG_ENV = "TOWOW_BOOTSTRAP_CANARY"


def bootstrap_canary_enabled() -> bool:
    """Gate: bootstrap canary / health enforcement on? Default off (gated on owner ignition).

    Mirrors `_exec_isolation_enabled` (TOWOW_EXEC_ISOLATION): off (default) = a guaranteed
    no-op so wiring this into the live auto-promote / daemon-start path never disrupts the
    running autopilot until owner turns it on (段⑥ 受控点亮同闸). on = enforce.
    """
    return os.environ.get(_CANARY_FLAG_ENV, "").strip().lower() in {"on", "1", "true"}


# ════════════════════════════════════════════════════════════════════════════════
#  Core-touching classifier (§4.3 ② "碰 deploy/heartbeat/dispatch 核心的改动先 canary")
# ════════════════════════════════════════════════════════════════════════════════

# deploy / heartbeat / dispatch 核心器官 —— 碰这些路径的 promote 候选才要跑 canary. 用 path
# 子串匹配 (changed file 可能带 'harness/src/' 前缀, 子串吸收前缀). 活清单: 新增自更新/派发核心
# 器官时同步加进来 (与 new-capability-task-classifier 信号① 的能力路径集同性质).
_CORE_PATH_SUBSTRINGS: tuple[str, ...] = (
    "towow/l2/orchestrator.py",        # heartbeat (run_polling_loop) + dispatch (_route_event) + auto-promote
    "towow/l2/execution_dispatch.py",  # dispatch 核心 (compute_ready_tasks / select_dispatch_batch)
    "towow/l2/daemon_run_once.py",     # 心跳一拍 (run_once / commit mutex)
    "towow/shell/worktree.py",         # deploy 核心 (promote_to_main / promote_and_record)
    "towow/shell/bootstrap_canary.py",  # canary 自身 (坏的更新不能弄坏自我保护能力本身)
)


@dataclass(frozen=True)
class CoreTouchResult:
    """Whether a promote candidate touches deploy/heartbeat/dispatch core (→ canary required)."""

    canary_required: bool
    matched_paths: list[str]
    reason: str


def classify_core_touching(changed_files: Iterable[str] | None) -> CoreTouchResult:
    """Fail-closed: does this change touch deploy/heartbeat/dispatch core → must canary?

    fail-closed 偏严 (镜像 new-capability-task-classifier): `changed_files is None` (拿不到 diff
    清单) → canary_required=True (宁可多跑一次 canary, 不漏放一个砖机风险的核心改动).
    空清单 (确知零改动) → 不要求. 命中任一核心路径子串 → 要求.
    """
    if changed_files is None:
        return CoreTouchResult(
            canary_required=True,
            matched_paths=[],
            reason="changed_files 未知 → fail-closed 要求 canary (宁可误拦不漏放)",
        )
    files = list(changed_files)
    matched = [
        f for f in files
        if any(core in f.replace("\\", "/") for core in _CORE_PATH_SUBSTRINGS)
    ]
    if matched:
        return CoreTouchResult(
            canary_required=True,
            matched_paths=matched,
            reason=f"碰 deploy/heartbeat/dispatch 核心 {len(matched)} 个文件 → 须 canary",
        )
    return CoreTouchResult(
        canary_required=False,
        matched_paths=[],
        reason="未碰任何核心器官 (纯外围改动) → 不要求 canary",
    )


def git_changed_files(repo: Path, base_ref: str, head_ref: str = "HEAD") -> list[str] | None:
    """Diff name-only between base_ref..head_ref in `repo`. None on git error (→ fail-closed)."""
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "diff", "--name-only", f"{base_ref}...{head_ref}"],  # noqa: S607
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


# ════════════════════════════════════════════════════════════════════════════════
#  The one smoke core — "候选代码能 import + 心跳一拍 + 派发算一次, 不崩"
#  (§4.3 ② "一个测试 harness 实例跑新代码, 验它自己的'自部署+心跳+派发'还活")
# ════════════════════════════════════════════════════════════════════════════════

# 在【独立子进程】里加载候选代码 (PYTHONPATH=候选 src) 跑这段. 子进程退出码是机器真值 ——
# 被审者用任何文字伪造不了一个 subprocess 的真实 import/run 结果 (同 execution_done_recompute
# 起 pytest subprocess 复算的范式). 三个能力各自真跑:
#   self-deploy: import deploy 核心 (WorktreeManager.promote_to_main 可调)
#   heartbeat  : 跑一拍真 daemon 轮询 (run_polling_loop max_iterations=1 mock_spawn=True) ——
#                它走 ensure_layout / restart 检测 / write pid / reap / 一轮 scan 全套启动机器
#   dispatch   : compute_ready_tasks 在 fixture 上算一次, 不崩
_SMOKE_SCRIPT = r"""
import sys, json, tempfile
from pathlib import Path

def _fail(cap, exc):
    sys.stderr.write("SMOKE_FAIL cap=%s %s: %s\n" % (cap, type(exc).__name__, exc))
    raise SystemExit(3)

# self-deploy 核心活着?
try:
    from towow.shell.worktree import WorktreeManager
    assert callable(getattr(WorktreeManager, "promote_to_main", None)), "promote_to_main 不可调"
except Exception as e:  # noqa: BLE001
    _fail("self_deploy", e)

# dispatch 核心活着?
try:
    from towow.l2.execution_dispatch import compute_ready_tasks
    assert compute_ready_tasks([], "smoke-plan", set()) == [], "compute_ready_tasks 非空"
except Exception as e:  # noqa: BLE001
    _fail("dispatch", e)

# heartbeat 核心活着? —— 跑一拍真轮询 (mock spawn, 1 迭代) 在 throwaway .towow
try:
    from towow.l0.event_log import EventLog
    from towow.l2.orchestrator import run_polling_loop
    scratch = Path(tempfile.mkdtemp(prefix="bootstrap-smoke-"))
    (scratch / ".towow").mkdir()
    log = EventLog(scratch / ".towow" / "events.log")
    n = run_polling_loop(
        scratch / ".towow", log,
        poll_interval_s=0.0, mock_spawn=True, max_iterations=1,
    )
    assert n == 1, "run_polling_loop 未跑满 1 迭代: %r" % (n,)
except Exception as e:  # noqa: BLE001
    _fail("heartbeat", e)

sys.stdout.write("SMOKE_OK\n")
raise SystemExit(0)
"""

_SMOKE_CAPABILITIES = ("self_deploy", "heartbeat", "dispatch")


@dataclass(frozen=True)
class SmokeResult:
    """Outcome of running the candidate code's self-deploy+heartbeat+dispatch smoke."""

    passed: bool
    returncode: int
    stderr_tail: str
    checked: list[str] = field(default_factory=lambda: list(_SMOKE_CAPABILITIES))


def run_smoke(
    candidate_src: Path,
    *,
    timeout_s: float = 180.0,
) -> SmokeResult:
    """Run the 3-capability smoke as an isolated subprocess loading `candidate_src`'s towow.

    candidate_src: the directory containing the candidate `towow` package (e.g.
        `<worktree>/harness/src` for the auto-promote canary, or a scratch src for tests).
        Prepended to PYTHONPATH so the candidate code wins over any installed copy; the smoke
        also asserts it loaded from candidate_src (defends against an editable-install shadow).

    A broken candidate (syntax/import error in a core file, a removed dispatch fn, a heartbeat
    that crashes on the first tick) → subprocess non-zero exit → passed=False. The returncode +
    captured stderr are the machine truth; nothing here trusts the candidate's self-report.
    """
    candidate_src = candidate_src.resolve()
    existing = os.environ.get("PYTHONPATH", "")
    pythonpath = str(candidate_src) + (os.pathsep + existing if existing else "")
    # 加一道 candidate-pinning 前置: 确认真从 candidate_src 加载 towow, 不是被装好的 live 副本截胡.
    pin = (
        "import towow, sys\n"
        f"_cand = {str(candidate_src)!r}\n"
        "if not str(getattr(towow, '__file__', '')).startswith(_cand):\n"
        "    sys.stderr.write('SMOKE_FAIL cap=pin towow loaded from %s not %s\\n' "
        "% (towow.__file__, _cand)); raise SystemExit(4)\n"
    )
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", pin + _SMOKE_SCRIPT],
            env={**os.environ, "PYTHONPATH": pythonpath, "PYTHONWARNINGS": "ignore"},
            cwd=str(candidate_src),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return SmokeResult(
            passed=False,
            returncode=-1,
            stderr_tail=f"smoke timeout after {timeout_s}s (候选代码卡死/起不来): {exc}",
        )
    except OSError as exc:
        return SmokeResult(passed=False, returncode=-2, stderr_tail=f"smoke spawn OSError: {exc}")
    tail = (proc.stderr or proc.stdout or "")[-800:]
    return SmokeResult(passed=proc.returncode == 0, returncode=proc.returncode, stderr_tail=tail)


# ════════════════════════════════════════════════════════════════════════════════
#  Event emission (NodeTouched stub-rewrap, path-B write_direct, non-interactive prov)
#  —— mirror shell/worktree.py `_emit_worktree_promoted` + l2/orchestrator `_build_orch_nodetouched`
# ════════════════════════════════════════════════════════════════════════════════

# done_criteria 要求事件 provenance 非交互 (daemon/orchestrator 自动产出, 非人手 CLI session).
# canary/health/rollback 都跑在 orchestrator auto-promote / daemon-start 路径里, 故 actor 用与
# orchestrator 同一身份 (SYSTEM / f11-orchestrator-polling) —— review/audit 据此断言非交互.
_BOOTSTRAP_ACTOR_TYPE = "system"
_BOOTSTRAP_ACTOR_ID = "f11-orchestrator-polling"

KIND_CANARY = "BootstrapCanaryEvaluated"
KIND_HEALTH = "BootstrapHealthChecked"
KIND_ROLLBACK = "BootstrapRolledBackToLkg"
KIND_LKG = "BootstrapLkgRecorded"


def _emit_bootstrap_event(
    event_log: EventLog,
    *,
    kind: str,
    decision_id: str,
    body: dict[str, object],
) -> str:
    """Emit a bootstrap-protection audit event (NodeTouched stub, path-B write_direct, NOT gated).

    write_direct (path-B): callers may run inside the global commit mutex (auto-promote does) —
    attempt_commit would re-acquire that fcntl lock → self-deadlock (M-3.1 §5 red-line, same as
    `_emit_worktree_promoted`). write_direct only touches events.log.lock → composes safely.
    """
    from towow.schemas.enums import (
        BaseClassification,
        EventCategory,
        EventType,
        SubjectEntityType,
        SubjectRole,
    )
    from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

    intent = EventIntent(
        local_intent_id=f"bootstrap-{uuid.uuid4().hex[:12]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "target_entity_type": "task",
            "target_entity_id": decision_id,
            "touch_type": "write",
            "kind": kind,
            "stub_original_payload": {"kind": kind, **body},
        },
        provenance_hint=ProvenanceHint(
            actor_type=_BOOTSTRAP_ACTOR_TYPE,
            actor_id=_BOOTSTRAP_ACTOR_ID,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.TASK,
                entity_id=decision_id,
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )
    rec = event_log.write_direct(intent)
    return rec.event_id


# ════════════════════════════════════════════════════════════════════════════════
#  LKG (last-known-good) tracking
# ════════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LkgRecord:
    """The last main commit whose code passed the bootstrap smoke (safe to fall back to)."""

    commit_sha: str
    recorded_at_unix: float
    reason: str


def _bootstrap_dir(towow_dir: Path) -> Path:
    d = towow_dir / "bootstrap"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lkg_path(towow_dir: Path) -> Path:
    return _bootstrap_dir(towow_dir) / "last_known_good.json"


def read_lkg(towow_dir: Path) -> LkgRecord | None:
    """Read the recorded last-known-good commit (None if never recorded)."""
    p = lkg_path(towow_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("commit_sha"), str):
        return None
    return LkgRecord(
        commit_sha=str(data["commit_sha"]),
        recorded_at_unix=float(data.get("recorded_at_unix", 0.0)),
        reason=str(data.get("reason", "")),
    )


def record_lkg(
    towow_dir: Path,
    event_log: EventLog,
    *,
    commit_sha: str,
    reason: str,
    now_unix: float | None = None,
) -> LkgRecord:
    """Record `commit_sha` as the new last-known-good (smoke passed) + emit BootstrapLkgRecorded.

    atomic write (tmp+rename, same crash-safety as watermark/spawn_mode).
    """
    rec = LkgRecord(
        commit_sha=commit_sha,
        recorded_at_unix=now_unix if now_unix is not None else time.time(),
        reason=reason,
    )
    p = lkg_path(towow_dir)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {"commit_sha": rec.commit_sha, "recorded_at_unix": rec.recorded_at_unix,
             "reason": rec.reason},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tmp.replace(p)
    _emit_bootstrap_event(
        event_log,
        kind=KIND_LKG,
        decision_id=f"lkg-{commit_sha[:12]}",
        body={"commit_sha": commit_sha, "reason": reason},
    )
    return rec


# ════════════════════════════════════════════════════════════════════════════════
#  Rollback — code-only restore of src to LKG (parameterized to target_repo; NEVER the live tree)
# ════════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of restoring a repo's source tree to the last-known-good commit."""

    rolled_back: bool
    lkg_commit: str
    src_path: str
    detail: str


def rollback_to_lkg(
    target_repo: Path,
    lkg_commit: str,
    event_log: EventLog,
    *,
    src_path: str = "harness/src",
    target_id: str,
    trigger: str,
) -> RollbackResult:
    """Code-only restore of `target_repo/{src_path}` to `lkg_commit` + forward-commit. Emits rollback.

    ★ 绝不碰 .towow (账本/投影 tracked 运行态) —— 只 checkout src_path 一棵子树到 LKG, 再
      forward-commit (新 commit, 不 reset / 不改历史, 账本停在当前). 这把"回滚到 LKG"做成与
      patch-serial-reflow 同向的 forward-only 动作, 躲开 2026-06-10 双事故那一类 (git reset/restore
      整树回退把账本也卷走). `target_repo` 是参数 —— 生产里只对 gated 恢复手术的 repo 跑, 永不自动
      碰 live 共享主树 (那条由 canary 'held at LKG' 兜: 坏代码压根没 promote 进 main).

    起不来的 src 被 `git checkout <lkg> -- <src_path>` 还原成 LKG 版本; .towow 不在 checkout
    路径里, 故账本 / 水位线 / 锁全不动.
    """
    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(target_repo), *args],  # noqa: S607
            check=True, capture_output=True, text=True, timeout=60,
        )

    try:
        # 只还原 src 子树到 LKG (不碰 .towow): checkout <commit> -- <path> 把 path 内容置回该 commit.
        _git("checkout", lkg_commit, "--", src_path)
        # forward-commit (allow-empty: 若 src 已等于 LKG 则空提交也留痕, 不当失败).
        _git(
            "-c", "user.email=bootstrap@towow", "-c", "user.name=bootstrap-canary",
            "commit", "--allow-empty", "-m",
            f"bootstrap rollback: restore {src_path} → LKG {lkg_commit[:12]} ({trigger})",
        )
        detail = f"git checkout {lkg_commit[:12]} -- {src_path} + forward-commit (账本未动)"
        ok = True
    except (subprocess.SubprocessError, OSError) as exc:
        stderr = getattr(exc, "stderr", "") or str(exc)
        detail = f"rollback git 失败 (fail-closed, 未还原): {str(stderr)[:300]}"
        ok = False

    _emit_bootstrap_event(
        event_log,
        kind=KIND_ROLLBACK,
        decision_id=f"rollback-{target_id}-{lkg_commit[:8]}",
        body={
            "target_id": target_id,
            "lkg_commit": lkg_commit,
            "src_path": src_path,
            "mode": "code_only_restore" if ok else "failed",
            "rolled_back": ok,
            "trigger": trigger,
            "detail": detail,
        },
    )
    return RollbackResult(rolled_back=ok, lkg_commit=lkg_commit, src_path=src_path, detail=detail)


# ════════════════════════════════════════════════════════════════════════════════
#  Call site 1 — canary gate (pre-promote; §4.3 ② 碰核心先 canary, 过了才 promote)
# ════════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CanaryDecision:
    """Whether the orchestrator should proceed to promote this worktree."""

    allow_promote: bool
    ran: bool
    smoke: SmokeResult | None
    reason: str


def canary_gate(
    towow_dir: Path,
    event_log: EventLog,
    *,
    worktree_path: Path,
    worktree_id: str,
    candidate_src: Path,
    changed_files: Iterable[str] | None,
    main_commit: str | None,
    trigger_event_id: str,
) -> CanaryDecision:
    """Pre-promote gate: if a core-touching change won't self-deploy/heartbeat/dispatch, hold at LKG.

    flag off → ran=False, allow_promote=True (guaranteed no-op, current behaviour preserved).
    flag on:
      - not core-touching → allow_promote=True (no smoke needed).
      - core-touching → run_smoke(candidate_src):
          pass → record LKG (this main_commit is good) + emit canary(passed) + allow_promote=True.
          fail → emit canary(failed) + emit rollback(mode=held_at_lkg, main 本就停在 LKG, 零 git
                 动作) + allow_promote=False (caller 不 promote, 工位留待人工 / fix).
    """
    if not bootstrap_canary_enabled():
        return CanaryDecision(allow_promote=True, ran=False, smoke=None,
                              reason="canary 未启用 (TOWOW_BOOTSTRAP_CANARY off) → no-op 放行")

    cls = classify_core_touching(changed_files)
    if not cls.canary_required:
        return CanaryDecision(allow_promote=True, ran=False, smoke=None,
                              reason=f"非核心改动 → 跳过 canary ({cls.reason})")

    smoke = run_smoke(candidate_src)
    _emit_bootstrap_event(
        event_log,
        kind=KIND_CANARY,
        decision_id=f"canary-{worktree_id}",
        body={
            "worktree_id": worktree_id,
            "result": "passed" if smoke.passed else "failed",
            "matched_core_paths": cls.matched_paths,
            "smoke_returncode": smoke.returncode,
            "smoke_stderr_tail": smoke.stderr_tail if not smoke.passed else "",
            "trigger_event_id": trigger_event_id,
            "candidate_src": str(candidate_src),
        },
    )
    if smoke.passed:
        if main_commit:
            record_lkg(towow_dir, event_log, commit_sha=main_commit,
                       reason=f"canary passed for worktree {worktree_id}")
        return CanaryDecision(allow_promote=True, ran=True, smoke=smoke,
                              reason="canary 通过 (自部署+心跳+派发还活) → 准 promote")

    # canary 失败: 不 promote → main 本就停在 LKG (held). 这是"回滚到 LKG"的 prevention 形态:
    # 零 git 动作 (坏代码压根没进 main), 故绝不碰共享树. emit rollback(held) 留痕.
    lkg = read_lkg(towow_dir)
    _emit_bootstrap_event(
        event_log,
        kind=KIND_ROLLBACK,
        decision_id=f"rollback-{worktree_id}-held",
        body={
            "target_id": worktree_id,
            "lkg_commit": lkg.commit_sha if lkg else (main_commit or "unknown"),
            "mode": "held_at_lkg",
            "rolled_back": True,
            "trigger": f"canary_failed worktree={worktree_id}",
            "detail": "canary 失败 → 不 promote, main 停在 LKG (零 git 动作, 坏代码未进 main)",
        },
    )
    return CanaryDecision(
        allow_promote=False, ran=True, smoke=smoke,
        reason=f"canary 失败 (候选起不来 rc={smoke.returncode}) → 不 promote, held at LKG",
    )


# ════════════════════════════════════════════════════════════════════════════════
#  Call site 2 — health gate (post-deploy / pre-daemon-start; §4.3 ① 起不来回 LKG)
# ════════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HealthDecision:
    """Whether the just-deployed code is healthy enough to (re)start the daemon on."""

    healthy: bool
    ran: bool
    smoke: SmokeResult | None
    rollback: RollbackResult | None
    reason: str


def health_gate(
    towow_dir: Path,
    event_log: EventLog,
    *,
    repo_root: Path,
    candidate_src: Path,
    current_commit: str | None,
    trigger: str,
    auto_rollback: bool = False,
) -> HealthDecision:
    """Post-deploy / pre-(re)start health check: deployed code can't start → emit + (gated) rollback.

    flag off → ran=False, healthy=True (no-op).
    flag on → run_smoke(candidate_src) on the *current/deployed* code:
      pass → record LKG (current_commit good) + emit health(passed) → healthy=True (准启动).
      fail → emit health(failed); if `auto_rollback` and an LKG exists → rollback_to_lkg(repo_root)
             (code-only src restore, 账本不动) + emit rollback; healthy=False.

    ★ auto_rollback 默认 False: 即便 enforcement 开了, 自动对 repo_root 跑 git restore 仍是危险
      动作 (repo_root 可能就是 live 共享主树) —— 默认只检出+告警+留痕, 把"真还原 src 到 LKG"留给
      gated 恢复手术 (owner / 受控启动 wrapper 显式传 auto_rollback=True + 一个非共享 target).
      测试显式传 auto_rollback=True + throwaway repo 验真还原.
    """
    if not bootstrap_canary_enabled():
        return HealthDecision(healthy=True, ran=False, smoke=None, rollback=None,
                             reason="health 未启用 (TOWOW_BOOTSTRAP_CANARY off) → no-op")

    smoke = run_smoke(candidate_src)
    _emit_bootstrap_event(
        event_log,
        kind=KIND_HEALTH,
        decision_id=f"health-{(current_commit or 'unknown')[:12]}",
        body={
            "result": "passed" if smoke.passed else "failed",
            "current_commit": current_commit,
            "smoke_returncode": smoke.returncode,
            "smoke_stderr_tail": smoke.stderr_tail if not smoke.passed else "",
            "trigger": trigger,
        },
    )
    if smoke.passed:
        if current_commit:
            record_lkg(towow_dir, event_log, commit_sha=current_commit,
                       reason=f"health passed ({trigger})")
        return HealthDecision(healthy=True, ran=True, smoke=smoke, rollback=None,
                             reason="health 通过 (部署的代码起得来) → 准启动 daemon")

    # 起不来: 回 LKG.
    lkg = read_lkg(towow_dir)
    rollback: RollbackResult | None = None
    if auto_rollback and lkg is not None:
        rollback = rollback_to_lkg(
            repo_root, lkg.commit_sha, event_log,
            target_id="daemon-restart", trigger=f"health_failed ({trigger})",
        )
    else:
        # 不自动还原 (默认): 仍 emit rollback(held/deferred) 留痕, 等 gated 恢复手术.
        _emit_bootstrap_event(
            event_log,
            kind=KIND_ROLLBACK,
            decision_id=f"rollback-restart-{(current_commit or 'x')[:8]}-deferred",
            body={
                "target_id": "daemon-restart",
                "lkg_commit": lkg.commit_sha if lkg else "none",
                "mode": "deferred_to_gated_recovery" if lkg else "no_lkg_available",
                "rolled_back": False,
                "trigger": f"health_failed ({trigger})",
                "detail": ("起不来但 auto_rollback off → 不自动碰 repo, 告警+留痕等 gated 恢复"
                           if lkg else "起不来且无 LKG 记录 → 无可回滚目标, 须人工介入"),
            },
        )
    return HealthDecision(
        healthy=False, ran=True, smoke=smoke, rollback=rollback,
        reason=f"health 失败 (部署的代码起不来 rc={smoke.returncode}) → 回 LKG",
    )


__all__ = [
    "KIND_CANARY",
    "KIND_HEALTH",
    "KIND_LKG",
    "KIND_ROLLBACK",
    "CanaryDecision",
    "CoreTouchResult",
    "HealthDecision",
    "LkgRecord",
    "RollbackResult",
    "SmokeResult",
    "bootstrap_canary_enabled",
    "canary_gate",
    "classify_core_touching",
    "git_changed_files",
    "health_gate",
    "lkg_path",
    "read_lkg",
    "record_lkg",
    "rollback_to_lkg",
    "run_smoke",
]
