"""R01 §2 — EvolutionaryCouplingMiner: 从变更历史挖演化耦合 (共变).

# concept source: c-evolutionary-coupling-mining@v1
#   从事件日志/git历史挖共变的工具协议。关联规则: support = 一起被改的提交/envelope 数,
#   confidence = support(规则)/support(前件); 输出概念级耦合边。
#   双用途: (a) 补候选影响集 CIS 的历史路; (b) 回填碎成 32 块的概念图的关联边 (结构自愈)。
#   已知坑: 纯计数不看"改动是否对应"会报假阳性 → 加对应性检查去假阳性。

挖的是**积累的历史**这种成熟技术 (Zimmermann et al. 2004, "Mining Version Histories to
Guide Software Changes"), 不是从单个 diff 瞎猜: 每个 commit / envelope 是一个**共变篮子**
(一组一起被改的节点), 跨整段历史做关联规则统计。

## 三个度量

- **support(规则 A∧B)** = 同时含 A 和 B 的篮子数 (共变次数)。
- **confidence(A→B)** = support(A∧B) / support(A) = "改了 A 时, 有多大比例也改了 B"。
  方向性: confidence(A→B) ≠ confidence(B→A)。
- **lift(A,B)** = P(A∧B) / (P(A)·P(B)) = support(A∧B)·N / (support(A)·support(B))。
  对称。lift>1 = 共变多于独立巧合; lift≈1 = 一方只是无处不在 (改啥都顺手带它)。

## 对应性检查 (去假阳性 — 本能力的 novelty)

纯 support/confidence 会把"无处不在的大文件" (如 cli/main.py: 几乎每个 commit 都动它)
报成所有东西的高置信耦合 —— 但那不是真耦合, 只是基率高。**对应性检查 = lift 阈值**: 一条
共变规则只有在 lift ≥ min_lift 时才判 `corresponds=True` (共变显著多于独立巧合), 否则视为
基率驱动的假阳性。harness 自身历史实证: 真耦合 (enums~registry / execution_dispatch~
orchestrator / capsule~pipeline) lift 都 ≥6, 而 `*→main` 这类假阳性 lift 全塌到 ≤1.8。

附 transitive 关联规则细化 (`flag_transitive`): A→C 若能被 A→B→C (B 是 hub) 充分解释,
标 `transitive_suspect`, 供下游进一步收口。这是 concept "已知坑" 列的第二种细化手段。

## 过估治理 (来自 goal 菠萝包)

SIS 小、CIS 系统算; 共变结果按 (corresponds, lift, confidence, support) 排序剪枝, 默认只
留 corresponding 的边回填, 避免假阳性污染概念图。
"""

from __future__ import annotations

import subprocess  # git by-PATH 调用, list-args + shell=False, 同 worktree.py precedent
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

# 默认对应性/剪枝阈值 (harness 自身历史标定: 真耦合 lift≥6, main 假阳性 lift≤1.8 → 2.0 干净分离)。
DEFAULT_MIN_SUPPORT = 2
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MIN_LIFT = 2.0

# git log 解析用的 commit 分隔哨兵 (pretty=format 注入, 不会与真实文件路径撞)。
_COMMIT_MARKER = "\x01commit\x01"


@dataclass(frozen=True)
class CouplingEdge:
    """一条有向概念级耦合边 (关联规则 source→target) + 其统计与对应性判定。

    lift 对称 (source/target 互换不变), 但 confidence 有向 —— 同一对共变会产两条边
    (A→B 与 B→A), 各带自己的 confidence。下游回填关联边时可用 `as_undirected_key` 去重。
    """

    source: str
    target: str
    support: int  # 同时含 source 与 target 的篮子数 (共变次数)
    support_source: int  # 含 source 的篮子数 (前件基数)
    support_target: int  # 含 target 的篮子数
    total_baskets: int  # 总篮子数 N (算 lift 用)
    confidence: float  # support / support_source
    lift: float  # support·N / (support_source·support_target)
    corresponds: bool  # 对应性检查判定: 真耦合 (非基率驱动假阳性) 吗

    def as_undirected_key(self) -> tuple[str, str]:
        """无向去重键 (回填关联边时用) —— source/target 排序后的有序对。"""
        return (self.source, self.target) if self.source <= self.target else (self.target, self.source)

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "support": self.support,
            "support_source": self.support_source,
            "support_target": self.support_target,
            "total_baskets": self.total_baskets,
            "confidence": self.confidence,
            "lift": self.lift,
            "corresponds": self.corresponds,
        }


@dataclass(frozen=True)
class MiningResult:
    """一次共变挖掘的产物: 排序剪枝后的有向耦合边 + 篮子统计。"""

    edges: tuple[CouplingEdge, ...]  # 已按 (corresponds, lift, confidence, support) 降序
    total_baskets: int  # 喂进来的篮子总数 (含单件篮子)
    co_change_baskets: int  # 含 ≥2 节点的篮子数 (真正产共变对的)
    distinct_pairs: int  # 不同的无向共变对数

    def corresponding(self) -> tuple[CouplingEdge, ...]:
        """只取通过对应性检查的边 (去假阳性后的真耦合)。"""
        return tuple(e for e in self.edges if e.corresponds)

    def neighbors(self, node: str, *, only_corresponding: bool = True) -> list[CouplingEdge]:
        """用途 (a): 补 CIS 历史路 —— 给定节点, 返回它历史上耦合到的节点边 (前件=node)。

        按 confidence 降序 (最该一起改的排前面), 供 candidate_impact_set 沿历史路扩 CIS。
        """
        pool = self.corresponding() if only_corresponding else self.edges
        out = [e for e in pool if e.source == node]
        out.sort(key=lambda e: (e.confidence, e.lift, e.support), reverse=True)
        return out

    def association_edges(self) -> list[tuple[str, str, float]]:
        """用途 (b): 回填概念图关联边 —— 无向 (a,b,strength) 列表, 已去重去假阳性。

        strength = 两个方向 confidence 的较大者 (强方向代表这对的耦合度)。只回填
        corresponding 的边, 避免基率假阳性污染碎成多块的概念图。
        """
        best: dict[tuple[str, str], float] = {}
        for e in self.corresponding():
            key = e.as_undirected_key()
            if e.confidence > best.get(key, -1.0):
                best[key] = e.confidence
        return [(a, b, s) for (a, b), s in sorted(best.items(), key=lambda kv: kv[1], reverse=True)]


class EvolutionaryCouplingMiner:
    """从共变篮子序列挖关联规则, 带 lift 对应性检查去假阳性。

    篮子来源与本类无关 —— 既可来自 git commit (见 `baskets_from_git`), 也可来自事件日志
    envelope (每个 envelope 的 touched 文件/概念集 = 一个篮子)。本类只认 `Collection[str]`。
    """

    def __init__(
        self,
        *,
        min_support: int = DEFAULT_MIN_SUPPORT,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        min_lift: float = DEFAULT_MIN_LIFT,
    ) -> None:
        if min_support < 1:
            raise ValueError("min_support 必须 ≥1 (support 是计数)")
        self.min_support = min_support
        self.min_confidence = min_confidence
        self.min_lift = min_lift

    def mine(self, baskets: Sequence[Collection[str]]) -> MiningResult:
        """跑一遍关联规则挖掘。

        - item support 在**全部**篮子上数 (单件篮子也算前件出现 —— 一个节点老是单独改,
          它的 confidence 自然低, 这是对的)。
        - pair support 只可能来自 ≥2 件的篮子。
        - lift 的 N = 全部篮子数。
        - 剪枝: 只保留 support ≥ min_support 的对; 每对产两条有向边。
        - 对应性: lift ≥ min_lift 且 confidence ≥ min_confidence 且 support ≥ min_support。
        """
        item_support: Counter[str] = Counter()
        pair_support: Counter[tuple[str, str]] = Counter()
        co_change_baskets = 0

        for basket in baskets:
            items = set(basket)
            for it in items:
                item_support[it] += 1
            if len(items) >= 2:
                co_change_baskets += 1
                for a, b in combinations(sorted(items), 2):
                    pair_support[(a, b)] += 1

        total = len(baskets)
        edges: list[CouplingEdge] = []
        for (a, b), pair_n in pair_support.items():
            if pair_n < self.min_support:
                continue
            sa, sb = item_support[a], item_support[b]
            lift = (pair_n * total) / (sa * sb) if sa and sb and total else 0.0
            for src, tgt, s_src, s_tgt in ((a, b, sa, sb), (b, a, sb, sa)):
                conf = pair_n / s_src if s_src else 0.0
                corresponds = pair_n >= self.min_support and conf >= self.min_confidence and lift >= self.min_lift
                edges.append(
                    CouplingEdge(
                        source=src,
                        target=tgt,
                        support=pair_n,
                        support_source=s_src,
                        support_target=s_tgt,
                        total_baskets=total,
                        confidence=conf,
                        lift=lift,
                        corresponds=corresponds,
                    )
                )

        # 过估治理: 排序剪枝 —— corresponding 优先, 再按 lift / confidence / support 降序。
        edges.sort(key=lambda e: (e.corresponds, e.lift, e.confidence, e.support), reverse=True)
        return MiningResult(
            edges=tuple(edges),
            total_baskets=total,
            co_change_baskets=co_change_baskets,
            distinct_pairs=len(pair_support),
        )


def flag_transitive(
    edges: Sequence[CouplingEdge],
    *,
    slack: float = 1.0,
) -> set[tuple[str, str]]:
    """transitive 关联规则细化 (concept "已知坑" 第二种手段): 标出可被传递解释的有向规则。

    A→C 若存在中介 hub B (B≠A,C) 使 confidence(A→B)·confidence(B→C) ≥ confidence(A→C)·slack,
    则 A→C 可能只是 "A 带 B、B 带 C" 的传递投影而非 A 与 C 的直接耦合, 标为 transitive_suspect。
    返回被标的 (source, target) 集合, 供下游决定是否进一步降权 (本函数不改原 edges)。

    slack ∈ (0,1] 放宽判据 (1.0 = 传递路径须不弱于直接规则才算可疑); slack 越小越激进。
    """
    conf: dict[tuple[str, str], float] = {(e.source, e.target): e.confidence for e in edges}
    suspects: set[tuple[str, str]] = set()
    for (a, c), direct in conf.items():
        if a == c:
            continue
        for b in {n for pair in conf for n in pair}:
            if b in (a, c):
                continue
            ab = conf.get((a, b))
            bc = conf.get((b, c))
            if ab is not None and bc is not None and ab * bc >= direct * slack:
                suspects.add((a, c))
                break
    return suspects


# ─────────────────────────────────────────────────────────────────────────────
# 篮子来源 — git 历史 (实证路径) 与 envelope 变更集
# ─────────────────────────────────────────────────────────────────────────────


def default_path_classifier(path: str) -> str | None:
    """默认把文件路径映射成节点 id = 模块名 (文件 stem); 丢弃测试 / 包标记 / 非 py。

    下游 (T-R01-7 共变回填) 可替换成 path→concept_id 的映射以直接产概念级边。
    """
    path = path.strip()
    if not path.endswith(".py"):
        return None
    leaf = path.rsplit("/", 1)[-1]
    if leaf.startswith("test_") or "/tests/" in path:
        return None
    stem = leaf[:-3]
    if stem in ("__init__", "__main__"):
        return None
    return stem


def parse_name_only_log(
    log_text: str,
    *,
    classify: Callable[[str], str | None] = default_path_classifier,
    marker: str = _COMMIT_MARKER,
) -> list[frozenset[str]]:
    """把 `git log --name-only --pretty=format:<marker>%H` 的输出解析成共变篮子。

    每个 commit 一个篮子 = 该 commit 改的文件经 `classify` 映射后的非空节点集。空篮子 (commit
    没碰任何被 classify 接受的文件) 丢弃。纯函数 —— 不碰 git, 可用合成输入直接单测。
    """
    baskets: list[frozenset[str]] = []
    current: set[str] = set()
    started = False
    for line in log_text.splitlines():
        if line.startswith(marker):
            if started and current:
                baskets.append(frozenset(current))
            current = set()
            started = True
            continue
        if not line.strip():
            continue
        node = classify(line)
        if node:
            current.add(node)
    if started and current:
        baskets.append(frozenset(current))
    return baskets


def baskets_from_git(
    repo_dir: str | Path,
    *,
    pathspec: str = "src",
    classify: Callable[[str], str | None] = default_path_classifier,
) -> list[frozenset[str]]:
    """跑 `git log --name-only` 取 repo 的共变篮子 (实证路径)。

    `pathspec` 限定历史范围 (默认 'src', 即只看源码改动)。测试由 `classify` 默认排除。
    用 list-args + shell=False (无注入面), 同 worktree.py 的 git 调用 precedent。
    """
    result = subprocess.run(  # noqa: S603
        ["git", "log", "--name-only", f"--pretty=format:{_COMMIT_MARKER}%H", "--", pathspec],  # noqa: S607
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_name_only_log(result.stdout, classify=classify)


def baskets_from_change_sets(change_sets: Iterable[Collection[str]]) -> list[frozenset[str]]:
    """把任意变更集序列 (如事件日志 envelope 各自的 touched 节点集) 归一成篮子。

    每个变更集 = 一个篮子; 空集丢弃。这是 envelope 路径的入口 —— 上游只需把每个提交
    envelope 的 touched 文件/概念集喂进来, 共变挖掘与 git 路径完全同构。
    """
    return [frozenset(cs) for cs in change_sets if cs]


# 模块自检入口: `python -m towow.l1.cia.evolutionary_coupling <repo_dir>` 打印 Top 真耦合。
def _summarize(repo_dir: str) -> str:  # pragma: no cover - 人工自检便利, 非测试路径
    miner = EvolutionaryCouplingMiner()
    result = miner.mine(baskets_from_git(repo_dir))
    lines = [
        f"baskets={result.total_baskets} co_change={result.co_change_baskets} pairs={result.distinct_pairs}",
        "Top corresponding couplings (conf / lift / supp):",
    ]
    seen: set[tuple[str, str]] = set()
    for e in result.corresponding():
        key = e.as_undirected_key()
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {e.confidence:.2f} / {e.lift:5.1f} / {e.support:>2}  {e.source} ~ {e.target}")
        if len(seen) >= 15:
            break
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "."
    print(_summarize(target))
