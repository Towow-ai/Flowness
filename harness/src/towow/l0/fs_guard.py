"""T-FIX-INC-01 (事故护栏) — 文件系统删除护栏: 让"静默扫掉真 .towow"物理不可能。

# 事故源 (01-reconciliation/INCIDENT-TOWOW-WIPE-2026-06-10.md):
#   2026-06-10 05:55:01 生产 canonical 账本 harness/.towow/ 被某进程整目录删除,
#   丢 6 天 5200+ 条事件。无任何告警、无任何门 — "不制造新的静默"红线的最大反例。
#
# 本模块 = 四层护栏的 layer1 (safe_rmtree 白名单断言) + layer2 (账本根哨兵) +
# layer3 的 EventLog 侧检查 (assert_event_log_open_allowed)。layer4 (自动备份) 在
# l0/event_log/backup.py。
#
# 护栏语义 (按强度降序):
#   绝对断言 (force 也不能绕):
#     A. resolve 后的目标自身是账本根 (直接含 events.log 文件或 events/ 子目录) → 拒。
#     B. 目标树深处存在【真】哨兵 (.ledger-root 且 canonical_path == 它当前位置) → 拒。
#        哨兵【拷贝】(canonical 指别处 — git worktree checkout 出来的 .towow 基线拷贝)
#        → 放行, 否则 worktree 清理永远失败。哨兵坏/读不出 → fail-closed 拒。
#     B2. (T-FIX-CONCERN-01 证伪收尾) 目标的任一【祖先】目录含真哨兵 → 账本子树全保护:
#        除非目标相对账本根的路径命中工位/暂存白名单段 (worktrees/snapshots/tmp/staging/
#        ...) 或目录名以 .tmp 结尾, 否则拒, force 不可绕。证伪抓的洞: safe_rmtree(
#        .towow/events, force=True) 此前能放行 — events 子目录自身不是账本根形状 (A miss),
#        树内无哨兵 (B miss, 哨兵在 .towow/ 根), force 绕白名单 (C bypass) → 删掉全部
#        hot 段。祖先哨兵坏/读不出 → fail-closed 拒; 拷贝哨兵 (canonical 指别处) → 放行。
#   白名单断言 (force + 非空理由可绕):
#     C. 目标必须在 system-tmp 下, 或路径段命中白名单 (worktrees/snapshots/tmp/...),
#        或目录名以 .tmp 结尾。
#
# 生产代码一切递归删除必须走 safe_rmtree / assert_rmtree_safe, 不许裸 shutil.rmtree。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

LEDGER_SENTINEL_NAME = ".ledger-root"

# 白名单路径段: 命中任意一段 = "暂存/工位/可再生"类目录, 允许递归删除。
# 这是 defense-in-depth 的最外层 — 账本根断言 (A/B) 永远先于它、且不可被 force 绕过。
_WHITELIST_SEGMENTS = frozenset(
    {
        "tmp",
        "worktrees",  # .towow/worktrees/<task> + .claude/worktrees/<run> 隔离工位
        "snapshots",  # M-0.7 snapshot 暂存/热区
        "snapshots-archive",  # M-0.7 snapshot 冷归档
        "staging",  # events/cold/staging 暂存
        "skills",  # .claude/skills 部署覆盖 (可再生: 从 package 源重铺)
        ".towow-backup",  # layer4 备份保留轮换
    },
)


class LedgerProtectionError(RuntimeError):
    """T-FIX-INC-01 fail-closed: 拒绝可能伤及 canonical 账本的文件系统操作。"""


class LedgerForkError(LedgerProtectionError):
    """f-ledger-canonical-fork-distributed-merge-design 出生门 fail-closed:

    上方已有【真 canonical】(其 .ledger-root 自指) 时, 拒绝在更深处新建第二个【自指的】
    .ledger-root —— 否则路径 bug 会再分叉出一个自称 canonical 的账本 (现状反例:
    harness/.towow 真 + harness/harness/.towow 假, 两个都自指)。RuntimeError 子类, 不是
    OSError, 所以会逃出 EventLog.__init__ 里那层 contextlib.suppress(OSError) —— 蓄意:
    在分叉位置开账本就该 fail-closed 炸出来, 而不是静默长出第二个真相源。
    """


def _system_tmp_root() -> Path:
    """system-tmp 根 (测试可 monkeypatch 以测白名单 miss 分支)。"""
    return Path(tempfile.gettempdir()).resolve()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def is_ledger_root(path: Path) -> bool:
    """目录是否"账本根"形状: 直接含 events.log 文件或 events/ 子目录。"""
    return (path / "events.log").is_file() or (path / "events").is_dir()


def _sentinel_self_references(ledger_dir: Path) -> bool:
    """ledger_dir/.ledger-root 是否存在且【自指】(canonical_path == 它当前位置) = 真 canonical 标记。

    坏/读不出/指别处 → False (无法【证明】是真 canonical)。解析与出生门都只认【可证明的】
    自指 canonical: 这样出生门只在"上方有可证明的真 canonical 时"拒新建第二个, 不因坏哨兵误拒;
    删除护栏 (assert_rmtree_safe) 那条线对坏哨兵仍 fail-closed 拒删, 两条线各自正确。
    """
    sentinel = ledger_dir / LEDGER_SENTINEL_NAME
    if not sentinel.exists():
        return False
    try:
        data = json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and data.get("canonical_path") == str(ledger_dir.resolve())


def resolve_canonical_ledger_root(start: Path) -> Path | None:
    """向上走到最近的【真 canonical】账本根 (.towow whose .ledger-root 自指), 找不到返 None。

    f-ledger-canonical-fork-distributed-merge-design 的"解析永远向上走": 给一个工作目录 (或一个
    .towow 账本目录), 从它开始逐层向上, 对每个祖先 project 目录查 <ancestor>/.towow/.ledger-root
    是否自指, 返回【最近的】那个真 canonical 的 .towow Path。这样路径 bug 给了更深的 project_dir
    时, 调用方仍能找回唯一的真 canonical, 不会各按各的深度长出第二个账本。
    """
    start = start.resolve()
    for proj in (start, *start.parents):
        ledger = proj / ".towow"
        if _sentinel_self_references(ledger):
            return ledger
    return None


def _ledger_birth_fork_violation(new_ledger_root: Path) -> Path | None:
    """出生门判据: 在 new_ledger_root 新建【自指】哨兵会不会分叉出第二个 canonical。

    🔴 只对【生产 (非 system-tmp) 账本】强制 —— 与 layer3 assert_event_log_open_allowed 同款
    system-tmp 哲学。理由: 嵌套独立账本在结构上和分叉无法区分, 但语境能:
      - system-tmp 下 = 测试/校验沙箱 (如 validation runner 在 TemporaryDirectory 里建
        parent/.towow + parent/cold52/.towow 多个【各自独立】的沙箱账本) → 合法, 放行。
      - 非 tmp = 真生产账本树。生产里合法隔离【永远】落白名单段 (worktrees/snapshots);
        非白名单的嵌套自指 = 路径 bug 分叉 (harness/.towow 之下又长 harness/harness/.towow,
        正是 live 现状那个 fork) → 拒。
    (测试可 monkeypatch _system_tmp_root 让 tmp 结构【看起来非 tmp】, 验非 tmp 分叉真被拒。)

    判据本体: 向上 (从 new_ledger_root 所在 project 目录的【父】开始, 严格在它上方) 找最近的真
    canonical。找到了 → 看从那个 canonical 的 project 目录到 new_ledger_root 的相对路径有没有
    穿过白名单隔离段 (worktrees/snapshots/staging/... — 合法工位/暂存): 穿过 = 合法隔离, 放行;
    没穿过 = 真分叉, 返回那个上方 canonical (= 违规)。上方无真 canonical → None (第一个/独立账本)。
    """
    resolved = new_ledger_root.resolve()
    if _is_under(resolved, _system_tmp_root()):
        return None  # system-tmp 沙箱: 嵌套独立账本合法, 出生门不管 (生产账本才保护)
    above = resolve_canonical_ledger_root(resolved.parent.parent)
    if above is None:
        return None
    rel_parts = resolved.relative_to(above.parent).parts
    if any(part in _WHITELIST_SEGMENTS for part in rel_parts):
        return None  # 账本内合法工位/暂存隔离 (worktrees/snapshots/...) — 不是分叉
    return above


def write_ledger_sentinel(ledger_root: Path) -> Path:
    """layer2 — 在账本根落/刷新哨兵文件 (.ledger-root)。

    哨兵记录自己的 canonical_path (resolve 后绝对路径)。safe_rmtree 深扫时靠
    "canonical_path 是否等于哨兵当前位置"区分【真账本】vs【git checkout 拷贝】:
      - 真账本: 位置匹配 → fail-closed 拒删 (force 也不行)。
      - worktree 拷贝: 位置不匹配 → 放行 (worktree 清理必须能跑)。
    canonical 不匹配时刷新 (仓库被移动后第一次 EventLog open 自动修正, 防护栏
    因 stale 路径静默失效)。哨兵是保护性标记, 不是 canonical 内容 — 直接写文件。
    """
    sentinel = ledger_root / LEDGER_SENTINEL_NAME
    resolved = str(ledger_root.resolve())
    if sentinel.exists():
        try:
            data = json.loads(sentinel.read_text(encoding="utf-8"))
            if data.get("canonical_path") == resolved:
                return sentinel  # 已是最新 — 不动 (避免每次 open 都改 mtime)
        except (OSError, json.JSONDecodeError):
            pass  # 坏哨兵 → 重写修复
    # ── 出生门 (f-ledger-canonical-fork-distributed-merge-design): 即将在此【新建/改写成
    #    自指】哨兵; 若是【生产账本】且上方已有真 canonical (非白名单隔离段) → fail-closed 拒,
    #    物理上禁止分叉出第二个自称 canonical 的账本 (system-tmp 沙箱豁免, 见
    #    _ledger_birth_fork_violation)。放在【幂等自指 return 之后】是蓄意的: 已自指的真
    #    canonical (含 live 那个待 owner 合并的散账本 fork) 走上面早返, 不进此门 → 既有账本
    #    永远开得起来 (A2 合并要能读它), 只挡【新分叉的诞生】。──────────────────────────────
    fork_above = _ledger_birth_fork_violation(ledger_root)
    if fork_above is not None:
        msg = (
            f"f-ledger-canonical-fork 出生门拒绝: 不能在 {resolved} 新建自指账本根 —— "
            f"上方已有真 canonical {fork_above} (其 .ledger-root 自指)。两个自指 canonical = "
            f"账本分叉 (脏基线 + 多车道 baseline 污染)。解析应向上走到上方那个真 canonical "
            f"(resolve_canonical_ledger_root), 而不是在更深处再造一个。若此处确属合法隔离工位, "
            f"应落在白名单段下 (worktrees/snapshots/...)。"
        )
        raise LedgerForkError(msg)
    sentinel.write_text(
        json.dumps(
            {
                "warning": (
                    "TOWOW CANONICAL LEDGER ROOT — DO NOT DELETE / 勿删: "
                    "此目录是 v3 唯一真相源事件账本。删除 = 不可逆丢失 canonical 事件 "
                    "(参见 INCIDENT-TOWOW-WIPE-2026-06-10)。safe_rmtree 遇本文件且 "
                    "canonical_path 匹配时 fail-closed 拒删。"
                ),
                "canonical_path": resolved,
                "created_at": datetime.now(tz=UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return sentinel


def _real_sentinel_hits(resolved_target: Path) -> list[str]:
    """深扫目标树, 返回【真】哨兵 (或坏到无法证明是拷贝的哨兵) 的位置列表。

    os.walk 默认忽略遍历错误 (权限等) — 扫不进去的子树不会让删除误炸,
    但哨兵文件本身读不出/坏 JSON 一律 fail-closed 计为命中。
    """
    hits: list[str] = []
    if not resolved_target.is_dir():
        return hits
    for dirpath, _dirnames, filenames in os.walk(resolved_target):
        if LEDGER_SENTINEL_NAME not in filenames:
            continue
        sentinel = Path(dirpath) / LEDGER_SENTINEL_NAME
        try:
            data = json.loads(sentinel.read_text(encoding="utf-8"))
            canonical = data.get("canonical_path")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            hits.append(f"{sentinel} (哨兵损坏, 无法证明是拷贝 → fail-closed)")
            continue
        if canonical == str(Path(dirpath).resolve()):
            hits.append(f"{sentinel} (canonical 匹配 = 真账本)")
        # canonical 指别处 → git worktree checkout 拷贝 → 放行
    return hits


def _ledger_ancestor_violation(resolved_target: Path) -> str | None:
    """B2 (T-FIX-CONCERN-01) — 目标是否落在真账本子树内且非工位/暂存段。

    扫 resolved_target 的每个祖先目录找哨兵:
      - 哨兵坏/读不出 → fail-closed, 返回违规说明 (无法证明不是真账本)。
      - 真哨兵 (canonical == 祖先位置): 目标相对该账本根的路径段若命中白名单
        (worktrees/snapshots/tmp/staging/... — 账本内合法工位/暂存) 或目标名以
        .tmp 结尾 → 放行; 否则返回违规说明 (账本子树全保护, force 不可绕)。
      - 拷贝哨兵 (canonical 指别处 — worktree checkout 的 .towow 基线) → 放行。
    无违规 → None。
    """
    for ancestor in resolved_target.parents:
        sentinel = ancestor / LEDGER_SENTINEL_NAME
        if not sentinel.exists():
            continue
        try:
            data = json.loads(sentinel.read_text(encoding="utf-8"))
            canonical = data.get("canonical_path")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return f"{sentinel} (祖先哨兵损坏, 无法证明是拷贝 → fail-closed)"
        if canonical != str(ancestor):
            continue  # 拷贝哨兵 (canonical 指别处) → 该祖先不是真账本根
        rel_parts = resolved_target.relative_to(ancestor).parts
        if (
            any(part in _WHITELIST_SEGMENTS for part in rel_parts)
            or resolved_target.name.endswith(".tmp")
        ):
            continue  # 账本内合法工位/暂存 (如 .towow/worktrees|snapshots|tmp|staging)
        return f"{sentinel} (canonical 匹配 = 真账本根, 目标在其子树内: {resolved_target})"
    return None


def assert_rmtree_safe(
    path: Path | str,
    *,
    force: bool = False,
    reason: str | None = None,
) -> Path:
    """递归删除前的护栏断言 (layer1+layer2)。通过 → 返回 resolve 后的目标路径。

    自定义删除循环 (如 WorktreeManager._rmtree) 调这个; shutil.rmtree 用户直接用
    safe_rmtree。断言细节见模块 docstring。
    """
    target = Path(path)
    resolved = target.resolve()

    # ── 绝对断言 A: 目标自身是账本根 (force 不可绕) ──────────────────────────
    if is_ledger_root(resolved):
        msg = (
            f"T-FIX-INC-01 拒删: {resolved} 是账本根 (直接含 events.log/events/)。"
            f"canonical 账本绝不允许递归删除 (INCIDENT-TOWOW-WIPE-2026-06-10)。"
            f"force 不能绕过此断言。"
        )
        raise LedgerProtectionError(msg)

    # ── 绝对断言 B: 目标树深处有真哨兵 (force 不可绕) ────────────────────────
    sentinel_hits = _real_sentinel_hits(resolved)
    if sentinel_hits:
        msg = (
            f"T-FIX-INC-01 拒删: {resolved} 树内含真账本哨兵, fail-closed: "
            f"{'; '.join(sentinel_hits)}。如确属误报 (如迷路账本残骸), 人工看过 target "
            f"后手动处理 — 代码路径绝不自动删。"
        )
        raise LedgerProtectionError(msg)

    # ── 绝对断言 B2: 目标在真账本子树内 (祖先含真哨兵, force 不可绕) ─────────
    # T-FIX-CONCERN-01 证伪收尾: safe_rmtree(.towow/events, force=True) 此前能放行 →
    # 删掉全部 hot 段。账本子树全保护: 仅账本内白名单工位/暂存段 (worktrees/snapshots/
    # tmp/staging/...) 可删, 其余 (events/graph/locks/...) 绝对拒。
    ancestor_violation = _ledger_ancestor_violation(resolved)
    if ancestor_violation:
        msg = (
            f"T-FIX-INC-01 拒删: {resolved} 位于真账本子树内, fail-closed: "
            f"{ancestor_violation}。账本子树 (events/graph/locks/...) 绝不允许递归删除 "
            f"(INCIDENT-TOWOW-WIPE-2026-06-10); force 不能绕过此断言。"
        )
        raise LedgerProtectionError(msg)

    # ── 白名单断言 C: tmp/工位/暂存类才可删; 否则要 force + 非空理由 ──────────
    whitelisted = (
        _is_under(resolved, _system_tmp_root())
        or any(part in _WHITELIST_SEGMENTS for part in resolved.parts)
        or resolved.name.endswith(".tmp")
    )
    if not whitelisted:
        if force and reason and reason.strip():
            return resolved  # 显式 force + 理由 → 放行 (理由进 traceback 上下文)
        msg = (
            f"T-FIX-INC-01 拒删: {resolved} 不在删除白名单 "
            f"(段: {sorted(_WHITELIST_SEGMENTS)} / system-tmp / *.tmp)。"
            f"确需删除请显式 safe_rmtree(path, force=True, reason='...')。"
        )
        raise LedgerProtectionError(msg)
    return resolved


def safe_rmtree(
    path: Path | str,
    *,
    force: bool = False,
    reason: str | None = None,
) -> None:
    """护栏版 shutil.rmtree — 生产代码递归删除的唯一入口。

    先 assert_rmtree_safe (账本根/哨兵/白名单三道断言), 通过才真删。
    路径不存在时与 shutil.rmtree 同语义 (FileNotFoundError) — 调用点自己 guard exists()。
    """
    import shutil

    target = Path(path)
    if not target.exists() and not target.is_symlink():
        raise FileNotFoundError(str(target))
    assert_rmtree_safe(target, force=force, reason=reason)
    shutil.rmtree(target)


def assert_event_log_open_allowed(log_path: Path) -> None:
    """layer3 — 测试进程物理隔离的 EventLog 侧检查。

    TOWOW_REAL_LEDGER_GUARD=1 (tests/conftest.py 全局 autouse 设置, 子进程继承) 时,
    EventLog 拒绝打开 system-tmp 之外的账本路径 — 测试永远不可能拿真账本当 fixture
    (cwd 错位 / 裸 Path('.towow') 相对路径在 pytest 进程里直接炸出来, 而不是静默
    读写真账本)。显式豁免: TOWOW_REAL_LEDGER_EXEMPT=1 (有意对真账本跑的工具脚本),
    或 TOWOW_REAL_LEDGER_ALLOWED=<冒号分隔的额外允许前缀>。
    生产进程不设 guard env → 此检查零行为。
    """
    if os.environ.get("TOWOW_REAL_LEDGER_GUARD") != "1":
        return
    if os.environ.get("TOWOW_REAL_LEDGER_EXEMPT") == "1":
        return
    ledger_root = log_path if log_path.is_dir() else log_path.parent
    resolved = ledger_root.resolve()
    if _is_under(resolved, _system_tmp_root()):
        return
    for prefix in filter(None, os.environ.get("TOWOW_REAL_LEDGER_ALLOWED", "").split(":")):
        if _is_under(resolved, Path(prefix).resolve()):
            return
    msg = (
        f"T-FIX-INC-01 layer3 拒开: 测试进程 (TOWOW_REAL_LEDGER_GUARD=1) 试图打开 "
        f"system-tmp 之外的账本 {resolved} — 测试不许拿真账本当 fixture "
        f"(INCIDENT-TOWOW-WIPE-2026-06-10 高嫌疑面: 测试以错位路径碰真账本)。"
        f"用 tmp_path; 确属有意需设 TOWOW_REAL_LEDGER_EXEMPT=1 或 "
        f"TOWOW_REAL_LEDGER_ALLOWED=<prefix>。"
    )
    raise LedgerProtectionError(msg)


__all__ = [
    "LEDGER_SENTINEL_NAME",
    "LedgerForkError",
    "LedgerProtectionError",
    "assert_event_log_open_allowed",
    "assert_rmtree_safe",
    "is_ledger_root",
    "resolve_canonical_ledger_root",
    "safe_rmtree",
    "write_ledger_sentinel",
]
