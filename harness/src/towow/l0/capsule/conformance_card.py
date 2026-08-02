"""把模块对账卡 (活标准) 塞进 capsule —— agent 一开工就拿到「这模块该长啥样 / 建没建 / 红点过没过期」。

# spec source: 01-reconciliation/LIVING-STANDARD-BUILD-PLAN.md (owner 2026-06-05)
#   T-LS build 第二根线: agent 干活前 capsule 自动把对应模块的对账卡塞给它
#   (治「agent 不查 spec / 忘强调」)。

这件事解决什么: 一个 session 要改 M-2.1 (consumer relation) 的代码, 它一开工应该知道
「这模块按标准该干嘛、现在是 built 还是 enforced、有没有红点 (上次核对后代码又动过 → 需重核)」。
不解决会坏在: agent 不查 spec、闷头改、把「上次审计认证过」的卡当现状用、漏掉自己正在
让卡过期这件事。

怎么识别 capsule 关于哪个模块: 拿这次 task 的 target_artifacts (要改哪些文件) 和 touched_nodes
(碰到哪些概念) 去跟 cards.json 每张卡的 code_paths 比 —— 路径前缀相交就算命中。识别不到
模块就不附卡 (优雅降级, 不报错, 不破坏任何既有 capsule)。

只读 conformance.py 的 load_cards + card_staleness, 不改它。cards.json 不存在 / 解析失败 /
git 不可用 → 安全跳过, 返回空串。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


# 对账卡数据默认位置 (相对仓库根) — 与 CLI `towow conformance status` 同一个 canonical 位置。
_DEFAULT_CARDS_REL = Path("01-reconciliation") / "conformance" / "cards.json"


def render_conformance_card_section(
    *,
    towow_dir: Path,
    target_artifacts: Iterable[str],
    touched_nodes: Iterable[str],
) -> str:
    """命中模块的对账卡渲染成一小段文本 (供塞进 capsule)。识别不到 / 任何故障 → 空串。

    towow_dir = projection store 的 .towow 目录 (pipeline 用 projection_store._dir.parent 传进来)。
    仓库根 = towow_dir 往上找含 .git 的那层 (v3 包在 harness/ 子目录, cards.json 在仓库根)。

    全程吞异常: 这是 additive 附加信息, 任何环节出问题都不许让 capsule 组装崩 (优雅降级)。
    """
    try:
        return _render_inner(
            towow_dir=towow_dir,
            target_artifacts=list(target_artifacts or []),
            touched_nodes=list(touched_nodes or []),
        )
    except Exception:  # noqa: BLE001 — 附加信息, 绝不让它把主 pipeline 带崩 (优雅降级硬要求)
        return ""


def _render_inner(
    *,
    towow_dir: Path,
    target_artifacts: list[str],
    touched_nodes: list[str],
) -> str:
    repo = _find_repo_root(towow_dir)
    cards_file = _locate_cards_file(towow_dir, repo)
    if cards_file is None:
        return ""  # cards.json 不存在 → 不附卡 (优雅降级)

    from towow.conformance import card_staleness, load_cards  # 只读, 不改它

    cards = load_cards(cards_file)
    if not isinstance(cards, list):
        return ""

    matched = _match_cards(cards, target_artifacts=target_artifacts, touched_nodes=touched_nodes)
    if not matched:
        return ""  # 识别不到模块 → 不附卡 (优雅降级)

    blocks: list[str] = []
    for card in matched:
        try:
            staleness = card_staleness(card, repo)
        except Exception:  # noqa: BLE001 — git 不可用等 → 红点判不出, 仍附卡 (无红点信息)
            staleness = {}
        blocks.append(_render_one_card(card, staleness))
    return "\n\n".join(b for b in blocks if b.strip())


def _find_repo_root(towow_dir: Path) -> Path:
    """从 .towow 目录往上找含 .git 的那层 (= 仓库根, git staleness 在这层算)。

    找不到就回退 towow_dir 的父 (惯例: .towow 在 harness/ 下, 父即 harness/; 再不行就用它本身)。
    """
    try:
        start = towow_dir.resolve()
    except OSError:
        start = towow_dir
    for cand in (start, *start.parents):
        if (cand / ".git").exists():
            return cand
    # 回退: .towow 的父目录 (没有 .git 的隔离测试环境也能算 staleness, 只是 git 查不到 → 无红点)。
    return start.parent if start.parent != start else start


def _locate_cards_file(towow_dir: Path, repo: Path) -> Path | None:
    """定位 cards.json: 依次试 仓库根 / .towow 父 / .towow 父的父。找不到返回 None。"""
    try:
        towow_parent = towow_dir.resolve().parent
    except OSError:
        towow_parent = towow_dir.parent
    bases = [repo, towow_parent, towow_parent.parent]
    seen: set[Path] = set()
    for base in bases:
        if base in seen:
            continue
        seen.add(base)
        cand = base / _DEFAULT_CARDS_REL
        if cand.exists():
            return cand
    return None


def _match_cards(
    cards: list[dict],
    *,
    target_artifacts: list[str],
    touched_nodes: list[str],
) -> list[dict]:
    """命中: task 的 target_artifacts 或 touched_nodes 与卡的 code_paths 前缀相交。

    路径前缀相交 = 一方是另一方的前缀 (e.g. task 改 `harness/src/towow/l0/event_log/event_log.py`,
    卡 code_path = `harness/src/towow/l0/event_log` → 卡是文件路径的前缀 → 命中)。touched_nodes
    (概念 id) 也按字符串 == / 前缀比 (有的卡 code_paths 含概念锚)。无任何信号 → 不命中 (空 list)。
    """
    signals = [s for s in (*target_artifacts, *touched_nodes) if isinstance(s, str) and s.strip()]
    if not signals:
        return []
    matched: list[dict] = []
    seen_ids: set[str] = set()
    for card in cards:
        if not isinstance(card, dict):
            continue
        code_paths = card.get("code_paths")
        if not isinstance(code_paths, list):
            continue
        if not any(
            _path_overlaps(signal, str(cp))
            for signal in signals
            for cp in code_paths
            if isinstance(cp, str) and cp.strip()
        ):
            continue
        mid = str(card.get("module_id", "")) or str(id(card))
        if mid in seen_ids:
            continue
        seen_ids.add(mid)
        matched.append(card)
    return matched


def _path_overlaps(signal: str, code_path: str) -> bool:
    """前缀相交: signal 在 code_path 之下 (signal 以 code_path/ 开头), 或反过来。也含相等。

    用 `/` 边界比, 防止 `.../event_log` 误命中 `.../event_logger`。
    """
    a = signal.strip().rstrip("/")
    b = code_path.strip().rstrip("/")
    if not a or not b:
        return False
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def _render_one_card(card: dict, staleness: dict) -> str:
    """一张卡渲染成几行: 模块/该干嘛/built/enforced/红点/上次核哪天/缺口。"""
    mid = str(card.get("module_id", "?"))
    name = str(card.get("name", "")).strip()
    should_do = str(card.get("should_do", "")).strip()
    built = str(card.get("built", "")).strip()
    enforced = str(card.get("enforced", "")).strip()
    gap_type = str(card.get("gap_type", "")).strip()
    gap_detail = str(card.get("gap_detail", "")).strip()

    stale = bool(staleness.get("stale"))
    verified = str(staleness.get("verified_date") or card.get("verified_date", "")).strip()
    code_changed = str(staleness.get("code_last_changed", "")).strip()
    dot = "🔴 需重核 (上次核对后代码又动过)" if stale else "🟢 新鲜"

    header = f"[模块对账卡 {mid}{(' — ' + name) if name else ''}] {dot}"
    lines = [header]
    if should_do:
        lines.append(f"  该长啥样: {should_do}")
    status_bits = []
    if built:
        status_bits.append(f"built={built}")
    if enforced:
        status_bits.append(f"enforced={enforced}")
    if status_bits:
        lines.append("  现状: " + " / ".join(status_bits))
    if gap_type and gap_type.lower() != "none":
        gap_line = f"  缺口({gap_type}): {gap_detail}" if gap_detail else f"  缺口: {gap_type}"
        lines.append(gap_line)
    if verified:
        suffix = f" (代码最近改: {code_changed})" if code_changed else ""
        lines.append(f"  上次核对: {verified}{suffix}")
    if stale:
        lines.append("  ⚠ 你改这模块前先把卡重新核一遍, 别把过期标记当现状; 改完记得卡需要重新认证。")
    return "\n".join(lines)


__all__ = ["render_conformance_card_section"]
