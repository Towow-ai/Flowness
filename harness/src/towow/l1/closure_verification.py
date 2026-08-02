"""M-1.6 §5 / §7.1 三段式 closure 对合约真验证 (RUN-036 T-L1-76 + T-L1-69 共享内核).

设计吸收协调者第五轮 T-L1-53 改判 (debt-34dd7c8ea8ba) + OPUS advisor 对抗压测的血泪教训:

> T-L1-53 的病根 = "门的通过判定 100% 来自被审者自报的字段, 门只检查这些字段彼此自洽"
> (验自洽 ≠ 验对得上合约/现实)。真治住的唯一标准: **门的通过判定里必须有一部分是门自己
> 在 worktree 真跑出来的、被审者用任何文字都伪造不了的结果。**

三道防线 (任一缺失即退化为 flag-only):

1. **覆盖性 (防捏造无关 / 省略 / 重复 condition 私货)**: 合约 closure_criteria / ripple_targets /
   forbidden_residuals 每条必须被 closure_result 按字面**一对一消费** cover (重复 condition 须各有
   独立 result, 不能一条 result 顶两条合约 criterion — RUN-037 advisor V4); closure_result 里出现
   合约没有的项不计入通过性 (T-L1-53 探针: 捏造 FABRICATED_UNRELATED criterion / 省略 forbidden_residual)。

2. **门用合约自带 pattern 复算 (核心牙齿, RUN-037 P6 — 不信被审者给的 pattern)**:
   - 复算参数 (grep pattern / test_selector) 来自**合约 ClosureCriterion** (reviewer 签 FindingCreated
     时定死), 不是被审者 resolve 时给的 — 堵住 P6: 被审者把 grep 指向与 condition 无关、恰好 0 命中的
     pattern 自洽通过。GREP/TEST → 门自己 subprocess 跑, 拿真结果; 门复算结果 != 被审者自报 → 拒 (抓撒谎)。
   - **合约 verification_method=grep 却没绑 verification_pattern/expected_occurrences (或 =test 没绑
     test_selector) = 合约自相矛盾** (声明可机器验却不给机器验参数) → 门 **fail-closed 拒** (非降级;
     RUN-037 advisor V1: 降级只配给本质不可机器验的方法, 合约缺陷该退回让 author 补)。
   - SCHEMA_CHECK / PROJECTION_CHECK / REPLAY / MANUAL_REASONING → 本质不可门独立机器复算 →
     not_recomputable → **禁走 resolved 强门 (降级 partial)**, 防 "所有 criterion 都填 manual_reasoning
     让门退化回纯自报" 的退化攻击。

3. **forbidden_residuals 用合约自带 pattern 门自己 grep (最硬)**: pattern 来自 reviewer 签的
   合约 (被审者不能换), check_method=grep 时门 subprocess 真跑, found_occurrences > 0 → 拒。
   这是 T-L1-76 done_criteria 的核心 "ripple/residual grep 应 0 occurrences 作 blocking_check 真拦"。

诚实边界 (明账, 不冒充 done):
- forbidden_residual 的搜索 path scope 由 ClosureForbiddenResidual.search_scope (reviewer 合约侧,
  受信) 控制; None → 全域 (默认 repo_dir 排除 .git/.towow)。门忽略 closure_result 侧的 search_path
  (被审者自报, 可指向空目录 → gaming hole 已关死); 该字段透传仅作审计留痕。
- consensus_respected / review_plan_respected 真验需要完整 envelope context (patches + concept_graph +
  review_plan projection), CLI 命令层不全 → 在 fix_completed_checks 层做框架, 完整 wire 挂债 (§11
  尺子第二阶段)。
- git_diff criterion (finding-machine-check-encoding-gap-1780563112 缺口 a) 需要 caller 注入
  "本任务 commit 集改动文件清单" runner (M-1.4 work complete 已接线, 从 run 的 PatchProposed
  worktree_commit_sha 派生); M-1.5 fix/finding-resolve/verify-step 路径未接线 → 对 git_diff
  criterion **fail-closed 明拒** (不静默降级), 接线挂明账。
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from towow.schemas.enums import (
    GATE_RECOMPUTABLE_VERIFICATION_METHODS,
    ClosureResidualCheckMethod,
    ClosureRippleSyncStatus,
    ClosureVerificationMethod,
    LedgerEventScope,
    OccurrenceComparator,
    ReviewClosureState,
)
from towow.schemas.payloads.finding import (
    ClosureContract,
    ClosureCriterion,
    search_scope_shape_defect,
)

_STRICT = ConfigDict(strict=True, extra="forbid")
# 被审者提交的 closure_result 从 JSON 文件读 — JSON 无 enum 类型, 故 input models 用 non-strict 让
# "synced" 等字符串 coerce 进 enum; extra=forbid 防偷塞字段 (含 RUN-037 删掉的 recompute — 旧工件被拒)。
_INPUT = ConfigDict(strict=False, extra="forbid")

# verification_method 门能从 worktree 独立复算的集合 (被审者用文字伪造不了结果)。
# 其余 (schema_check / projection_check / replay / manual_reasoning) 本质不可门独立复算 →
# not_recomputable → 禁走 resolved 强门 (降级)。注: GREP/TEST 但合约没绑结构化 spec 不走降级,
# 走 fail-closed (合约自相矛盾, 见 _recompute_criterion)。
# RUN-044: 集合下沉到 schemas.enums 作单一真值源 (M-1.3 planner gate / M-1.4 execution 自检共用,
# 防漂移); 此处 alias 保持原名与行为不变。
_GATE_RECOMPUTABLE_CRITERION_METHODS = GATE_RECOMPUTABLE_VERIFICATION_METHODS
_GATE_RECOMPUTABLE_RESIDUAL_METHODS = frozenset({ClosureResidualCheckMethod.GREP})

# ── 门 residual/criterion re-grep 的搜索范围: 结构性 artifact 白名单 (非黑名单排除) ──
# f-closure-residual-grep-selfhits-json-projection-mirror-whitelist (同失败模式第5次形态) 钉死:
# 账本/派生/投影状态会以**任意格式、任意位置**自引用归档 FindingCreated payload (任一 finding 按定义
# self-quote 自己的 forbidden_residual pattern), 门 re-grep 只要下降进去就自命中 → found>0 → 系统性
# 拒本可机器闭合的真闭合。黑名单枚举 (按目录名 .towow* / 按扩展名 *.idx/*.jsonl) 复发5次
# (.towow → .towow-backup → .towow-PRE-*-bak-* → .towow 外 *.idx 派生索引 → .towow 外 *.json 投影
# 镜像 finding_lifecycle.json), 每加一格只挡一格, 下个格式/位置重开洞。
#
# 唯一封类的修法 = **白名单圈定搜索范围**: 门只搜真 artifact (git 版本控制识别的源码/文档/测试),
# 账本/派生/投影运行态整类**默认不搜** (默认拒), 无论何格式何位置:
#   1. git artifact 白名单 (gate_grep_occurrences 用 `git grep --untracked`): 只搜 tracked + untracked
#      非-ignored 文件。所有 .towow 外的账本派生/投影孤儿 (towow_dir 误定位 repo 根产生的孤儿
#      events/index/*.idx / graph/*.json / 任意未来派生, 皆 gitignored 运行态) → 不在白名单 → 整类
#      结构性免疫, 一次封死"格式/位置"两个追赶维度。untracked 非-ignored 纳入 → 真源码残留 (含未
#      commit 的新源文件) 不失明 (对照准则: 白名单 ≠ 对真残留失明)。
#   2. .ledger-root 结构标记排除 (_ledger_root_excludes): .towow / .towow-PRE-* 账本家族是 **tracked**
#      (89 热分片 + 备份快照被 commit 进 git), 会落进 git 白名单 → 用账本 root 的角色标记文件
#      `.ledger-root` (每个 towow 账本 root 都有) 结构性发现并排除其子树, 而非枚举 `.towow` 目录名。
#      将来任何名字的 tracked 账本 root 自动免疫; scope 根自身即使带 .ledger-root (误定位事故物证)
#      也不自排 (排它=整树失明)。
_LEDGER_ROOT_MARKER = ".ledger-root"

ItemStatus = Literal["passed", "failed", "not_recomputable"]


# ════════════════════════════════════════════════════════════════════════════════
#  被审者提交的 closure_result schema (--closure-result-file 的形状)
# ════════════════════════════════════════════════════════════════════════════════


class CriterionResult(BaseModel):
    """被审者对一条 closure_criterion 的自报 (覆盖 + 自报通过 + 证据).

    RUN-037 P6: **不再含 recompute / verification_method** — 复算 pattern 与 method 由合约
    ClosureCriterion 定死, 被审者不能提供 pattern (否则可指向无关 pattern 自洽通过)。被审者只声明
    "我覆盖了这条 + 我认为它过了 + 证据"; 门用合约 pattern 自己复算, self_reported_passed 仅交叉核对
    (门复算 != 自报 → 拒抓撒谎)。
    """

    model_config = _INPUT

    criterion: str = Field(min_length=1)  # 必须字面对应合约 closure_criteria[i].condition
    self_reported_passed: bool  # 仅交叉核对, 不作判定 (门复算 != 自报 → 拒)
    evidence: str = Field(min_length=1)


class RippleResult(BaseModel):
    """被审者对一个 ripple_target 的同步声明。"""

    model_config = _INPUT

    artifact: str = Field(min_length=1)  # 必须字面对应合约 ripple_targets[i].artifact
    sync_status: ClosureRippleSyncStatus
    evidence: str | None = None  # not_applicable 必须带 evidence


class ResidualCheckResult(BaseModel):
    """被审者声明一个 forbidden_residual 已检查。门用合约自带 pattern + 合约侧 search_scope 自己跑, 这里只声明 cover."""

    model_config = _INPUT

    pattern: str = Field(min_length=1)  # 必须字面对应合约 forbidden_residuals[i].pattern
    # 审计留痕字段: 门忽略它 (被审者自报, 可指向空目录永远 pass — gaming hole 已关死)。
    # 门实际的扫描面只由合约侧 ClosureForbiddenResidual.search_scope 定 (见 docstring 诚实边界 / 段 3)。
    search_path: str | None = None


class ClosureResult(BaseModel):
    """被审者提交的 closure 验证结果 (--closure-result-file). 门据合约重算, 不信自报."""

    model_config = _INPUT

    criteria_results: list[CriterionResult] = Field(default_factory=list)
    ripple_results: list[RippleResult] = Field(default_factory=list)
    residual_check_results: list[ResidualCheckResult] = Field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════════
#  门复算结果
# ════════════════════════════════════════════════════════════════════════════════


class ItemVerdict(BaseModel):
    """对一个合约条目 (criterion / ripple / residual) 的门判定。"""

    model_config = _STRICT

    item: str
    kind: Literal["criterion", "ripple", "residual"]
    status: ItemStatus
    recomputed_by_gate: bool  # 门是否自己真跑了 (vs 只接收自报 / 覆盖性)
    detail: str


class ClosureVerificationOutcome(BaseModel):
    """三段式 closure 对合约验证总结果。

    strong_gate_passed=True 仅当: 覆盖性全过 + 所有可复算项门复算通过 + 无 not_recomputable 项。
    含 not_recomputable 项 → downgrade_required=True (resolved 强门不通过, 应降级 partial)。
    任一可复算项 failed → strong_gate_passed=False (fail-closed 拒)。
    """

    model_config = _STRICT

    strong_gate_passed: bool
    downgrade_required: bool
    verdicts: list[ItemVerdict] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════════════
#  门自己跑的 worktree 复算 helper (precedent: cli/main.py:2515 真跑 git 子进程做 fail-closed 验证)
# ════════════════════════════════════════════════════════════════════════════════


class GateGrepTimeout(Exception):
    """门 grep 在预算 (timeout) 内没扫完 — 扫描面过大, 与"真残留"和"一般 grep 错误"都不同判。

    f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive: 旧行为把
    TimeoutExpired 折进 occurrences=None → caller 判 failed, 与"有残留没修干净"同判 —
    真闭合被系统性拒发机器凭证。超时的诚实语义是"门在预算内复算不了", 不是"发现了残留":
    closure verify 捕它 → not_recomputable (降级, 不进 resolved 强门也不冒充 ripple_incomplete);
    review_finding 捕它 → fail-closed finding (迁移验证验不了 = 不放行, 该模块语义不变)。
    用异常而非哨兵返回值: 漏捕会 fail-loud 炸出来, 哨兵 (如 -1) 流进 `occ > 0` 类比较是静默漏放。
    """


def _git_toplevel(search_path: Path) -> Path | None:
    """search_path 所在 git 仓库顶层 (worktree root); 非 git → None (caller fail-closed).

    白名单以 git 版本控制界定 artifact — search_path 必须落在某个 git 仓库内才能结构性区分
    "真 artifact" 与 "账本/派生运行态"。生产所有 grep scope (repo_dir / 合约 search_scope /
    解耦包 extra_bases) 都在 git 仓库内; 非 git 是异常, 返回 None 让 caller fail-closed (安全方向)。
    """
    anchor = search_path if search_path.is_dir() else search_path.parent
    try:
        proc = subprocess.run(
            ["git", "-C", str(anchor), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    top = proc.stdout.strip()
    return Path(top) if top else None


def _ledger_root_excludes(top: Path, scope_rel: str) -> list[str]:
    """结构性发现 top 下所有 tracked 账本 root (含 .ledger-root 标记的目录), 返回排除其子树的 git
    pathspec (相对 top)。用账本 root 的角色标记 `.ledger-root` (非目录名枚举) —— 将来任何名字的
    tracked 账本 root 自动免疫, 不追赶命名变体 (同症状复发5次皆'黑名单又漏一格')。

    scope 根自身不自排: 误定位事故会在 scope 根 (repo 根) 直接落 .ledger-root, 排它 = 整树失明。
    只排 scope 内的**子**账本 root (如 repo 根下的 .towow / .towow-PRE-*, 它们各自带 .ledger-root)。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(top), "ls-files", "-z", "--",
             _LEDGER_ROOT_MARKER, f"**/{_LEDGER_ROOT_MARKER}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    scope_norm = scope_rel.strip("/")
    excludes: list[str] = []
    for marker in proc.stdout.split("\0"):
        marker = marker.strip()
        if not marker:
            continue
        ledger_dir = str(Path(marker).parent)  # 账本 root 目录 (相对 top); "." = top 自身
        if ledger_dir == ".":
            continue  # top 根自带 .ledger-root (误定位孤儿) → 不排整树
        if scope_norm and ledger_dir == scope_norm:
            continue  # 不排 scope 根自身
        excludes.append(f":(exclude){ledger_dir}")
    return excludes


def _pattern_dialect(pattern: str) -> Literal["ere", "perl"]:
    """判合约 pattern 属于哪个正则方言 —— POSIX ERE 够用 ("ere") 还是必须 PCRE ("perl").

    f-closure-gate-git-grep-ere-silently-drops-pcre-escapes: 门原先一律 `git grep -E` (POSIX ERE),
    而 ERE 不认 `\\s` / `\\d` / `\\w` / `\\b` 这类 PCRE-only 转义 —— 遇到时 git grep **不报错**, 直接
    rc=1 返回 (与"真的零残留"完全不可区分)。方向是 fail-open: expected_occurrences=0 / eq 的判据
    (含全部 forbidden_residual) 在完全未修复的产物上照样判通过, 判据退化成空门。

    判别规则 (保守, 宁可多路由到 -P): 转义后面跟 ASCII 字母 → 两方言解读不同 (`\\s` ERE=字面 s /
    PCRE=空白; `\\t` ERE=字面 t / PCRE=制表) → "perl"。标点转义 (`\\.` `\\(` `\\|` `\\\\` ...) 与
    GNU ERE 的 `\\<` `\\>` 词界两方言一致 (或只 ERE 认) → 留在 "ere", 不改既有判据的语义。
    `\\\\s` (转义过的反斜杠 + 字面 s) 不算 —— 故按字符走, 不用裸 `re.search(r"\\\\[A-Za-z]")`。
    """
    i = 0
    n = len(pattern)
    while i < n:
        if pattern[i] == "\\" and i + 1 < n:
            nxt = pattern[i + 1]
            if nxt.isascii() and nxt.isalpha():
                return "perl"
            i += 2  # 转义对整体跳过 (含 `\\\\`), 不让第二个反斜杠再当转义头
            continue
        i += 1
    return "ere"


def _has_bre_alternation_escape(pattern: str) -> bool:
    """pattern 里有没有裸 `\\|` —— BRE 的交替算子写法, 在门跑的 ERE/PCRE 下语义正好相反。

    finding-bre-ere-dialect-done-criteria-w1: `verification_pattern` 这个字段**从未声明过方言**,
    而门只会跑 ERE (`-E`) 或 PCRE (`-P`)。BRE 下 `\\|` 是交替算子 (`a\\|b` = a 或 b), ERE/PCRE 下
    `\\|` 是转义成**字面竖线字符** (`a\\|b` = 字面串 "a|b") —— 两种读法互斥。作者按 `grep -n 'a\\|b'`
    的 BRE 习惯写判据时, 门用 -E 复算会把交替算子吃成字面竖线, 命中数系统性归零: 语义已满足的正确
    产物被判 failed。方向是 fail-**closed** 假阴性 (与本模块另一条 f-closure-gate-git-grep-ere-
    silently-drops-pcre-escapes 的 fail-open 相反) —— 不是安全漏洞, 但会挡住正确工作, 且执行者看到
    "命中 0" 会去改本来就对的产物, 是**误导性判决**。

    只拦 `\\|`, 不拦其余 BRE 元转义 (`\\(` `\\)` `\\{` `\\+` `\\?`): 在 ERE 里 `\\(` 就是查字面括号的
    正常写法, 拦它会打死大量合法 pattern, 且无证据显示它致害。`\\|` 是唯一一个"两种读法都成立、错误
    读法还静默给出一个像结论的数"的。活账本实测语料 (6 处带 `\\|` 的 verification_pattern) **100%
    是交替意图, 零处是字面竖线**, 故拒算不误伤既有判据。

    按字符扫 (不用裸 `re.search(r"\\\\\\|")`): `\\\\|` 是"转义过的反斜杠 + ERE 交替算子", 完全合法,
    不能误判 —— 转义对必须整体跳过, 与 `_pattern_dialect` 同一扫法。
    """
    i = 0
    n = len(pattern)
    while i < n:
        if pattern[i] == "\\" and i + 1 < n:
            if pattern[i + 1] == "|":
                return True
            i += 2  # 转义对整体跳过 (含 `\\\\`), 不让第二个反斜杠再当转义头
            continue
        i += 1
    return False


def _looks_like_missing_pcre(stderr: str) -> bool:
    """git 报的错是不是"这个 build 没有 PCRE" (而非 pattern 本身写错)。

    git 的原话按版本有差 ("cannot use Perl-compatible regexes when not compiled with USE_LIBPCRE" /
    "... not compiled with PCRE support"), 故按关键词判而非整句匹配。
    """
    low = stderr.lower()
    return "perl-compatible" in low or "libpcre" in low or ("pcre" in low and "compiled" in low)


def gate_grep_occurrences(pattern: str, search_path: Path) -> tuple[int | None, str]:
    """门自己 subprocess 跑 `git grep`, 返回 (occurrences, detail). None = 无法执行 (fail-closed).

    结构性 artifact 白名单 (见模块顶部 _LEDGER_ROOT_MARKER 注释): 只搜 git 版本控制识别的真 artifact
    (tracked + untracked 非-ignored), 账本/派生/投影运行态整类默认不搜 (无论何格式何位置)。`git grep
    --untracked` 天然免疫所有 .towow 外 gitignored 孤儿 (误定位产生的 events/index/*.idx / graph/*.json
    / 任意派生); .towow/.towow-PRE-* 家族是 tracked, 经 .ledger-root 结构标记排除。

    正则方言按 pattern 路由 (_pattern_dialect): 含 PCRE-only 转义 → `-P` (perl_regexp), 否则 `-E`。
    **路由是单向的**: `-P` 跑不起来 (git 未编译 PCRE) 绝不回退 `-E` —— 回退就是把"这个方言我算不了"
    重新伪装成"0 命中", 正是本 finding 要封的 fail-open。

    同一条禁令的另一面 (finding-bre-ere-dialect-done-criteria-w1): pattern 含裸 `\\|` 时 BRE 与
    ERE/PCRE 读法相反 (交替算子 vs 字面竖线), 门无从判作者意图 —— **拒算, 不替作者重新解释**
    (把 `\\|` 归一成 `|` 或改路由到 `-G` 都是门替作者改写他的 pattern, 与"不猜"同禁)。守卫排在
    `search_path.exists()` 之前: pattern 方言歧义是**合约缺陷**, 与产物在不在无关、必须无条件改掉;
    而"路径不存在"本来就不会静默, 让合约缺陷先说话不会盖掉任何原本可见的信息。

    Raises:
        GateGrepTimeout: git grep 超时预算没扫完 (≠真残留 ≠一般错误, caller 按各自语义区分处理)。
    """
    if _has_bre_alternation_escape(pattern):
        # 与 missing-PCRE 分支同类: "这条 pattern 本身没法算" → 硬 fail-closed (非 GateGrepTimeout
        # 那种 not_recomputable 降级 —— 超时是随负载来去的暂态, 方言歧义重跑一万次都一样)。
        return None, (
            f"ambiguous_regex_dialect: pattern 含裸 `\\|` — BRE 读作交替算子, 门跑的 ERE/PCRE 读作"
            f"字面竖线 (语义相反), 门不替作者猜意图, fail-closed: {pattern!r}. "
            f"要交替写 `|`, 要字面竖线写 `[|]`"
        )
    if not search_path.exists():
        return None, f"search_path 不存在: {search_path}"
    top = _git_toplevel(search_path)
    if top is None:
        return None, (
            f"search_path 不在 git 仓库内 — artifact 白名单无法界定 (账本/派生运行态 vs 真 artifact), "
            f"fail-closed: {search_path}"
        )
    try:
        scope_rel = str(search_path.resolve().relative_to(top.resolve()))
    except ValueError:
        scope_rel = "."
    pathspec = scope_rel if scope_rel != "." else "."
    excludes = _ledger_root_excludes(top, scope_rel)
    # `git grep --untracked --no-color -{E|P}Ioh`: --untracked 纳入未 commit 的新源码 (对照准则:
    # 真残留不失明), 但仍尊重 .gitignore (运行态孤儿不搜); -I 跳二进制; -o 每命中一行; -h 去文件名
    # 前缀 (数命中数); -E/-P 由 _pattern_dialect 按 pattern 里的转义方言选 (PCRE-only 转义走 -P)。
    dialect = _pattern_dialect(pattern)
    dialect_flag = "-P" if dialect == "perl" else "-E"
    args = ["git", "-C", str(top), "grep", "--untracked", "--no-color", dialect_flag, "-I", "-o", "-h",
            "-e", pattern, "--", pathspec, *excludes]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GateGrepTimeout(
            f"门 git grep 超时 (30s 预算内没扫完 {search_path} — 扫描面过大, "
            f"≠真残留): pattern={pattern!r}",
        ) from exc
    except (FileNotFoundError, OSError) as exc:
        return None, f"git grep 跑失败: {exc!r}"
    if proc.returncode == 0:
        count = len([ln for ln in proc.stdout.splitlines() if ln.strip()])
        return count, (
            f"git grep ({dialect}) 命中 {count} occurrences of {pattern!r} under {search_path} "
            f"(artifact 白名单)"
        )
    if proc.returncode == 1:
        return 0, f"git grep ({dialect}) 0 occurrences of {pattern!r} under {search_path} (artifact 白名单)"
    stderr = proc.stderr.strip()
    if dialect == "perl" and _looks_like_missing_pcre(stderr):
        # 本机 git 没编译 PCRE → 这条 pattern 的方言算不了。**不回退 -E** (回退 = 把"算不了"重新
        # 伪装成 0 命中, 正是本 finding 封的洞), 也不走 GateGrepTimeout 那种 not_recomputable 降级:
        # 超时是随机器负载来去的暂态, 而"引擎不支持这个方言"是硬性不兼容, 重跑一万次也一样 —— 降级
        # 会让它无声地长期停在 partial 通道; 判 failed 才逼人当场处置 (升级 git, 或按
        # f-fix-complete-gate-ignores-amended-closure-contract 的通道改写合约 pattern)。
        return None, (
            f"unsupported_regex_dialect: pattern 含 PCRE-only 转义需 `git grep -P` (perl_regexp), "
            f"但本机 git 未编译 PCRE 支持 — 门无法复算, fail-closed (绝不回退 -E 冒充 0 命中): "
            f"pattern={pattern!r} stderr={stderr[:120]!r}"
        )
    return None, f"git grep 错误 (rc={proc.returncode}, {dialect}): {stderr[:160]!r}"


def _pytest_extra_flags(project_root: Path) -> list[str]:
    """探测一个 uv 项目跑 pytest 需不需要 `--extra <name>` (中立性不变量①: 解耦包是独立 uv 项目).

    finding-gw1-machine-check-path-defect (a): harness 把 pytest 放 `[dependency-groups].dev`
    (uv `run` 默认带), 故不需 --extra; 但解耦在 harness/ 外的中立配件 (feishu_gateway /
    feishu_owner_channel) 按中立性把 pytest 放 `[project.optional-dependencies].test` —— uv 默认
    **不**装 optional extra, 不带 --extra 则 `python -m pytest` 报 "No module named pytest" →
    门复算假阴性 (本可机器闭合的真闭合被系统性拒)。规则: pytest 出现在某 optional-dependencies
    extra 里 → 返回该 extra 的 `--extra <name>` (取第一个命中的); 否则 [] (pytest 已在默认依赖/dev
    group 可达, 如 harness)。读 pyproject.toml 是纯文件读 (无 subprocess), 解析失败 → [] 退回默认行为。
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project", {})
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    if not isinstance(optional, dict):
        return []
    for extra_name, deps in optional.items():
        if not isinstance(deps, list):
            continue
        if any(isinstance(d, str) and re.match(r"^pytest(\W|$)", d.strip()) for d in deps):
            return ["--extra", str(extra_name)]
    return []


def gate_pytest(selector: str, repo_dir: Path) -> tuple[bool | None, str]:
    """门自己 subprocess 跑 pytest selector, 返回 (passed, detail).

    passed 三态 —— 与 gate_grep_occurrences/GateGrepTimeout 同哲学 (区分"门没拿到 pass/fail 信号"
    与"真失败"):
      * True  = 真通过 (pytest rc=0)。
      * False = 真断言失败 (pytest rc=1) —— criterion 确实未满足, fail-closed 判据。
      * None  = **门没拿到 pass/fail 信号** (not_recomputable): pytest 非-run rc (中断=2 / 内部错=3 /
                usage-error=4 / no-tests-collected=5 / 其它非 {0,1}), 或子进程根本没跑起来 (uv/pytest
                缺失 / 超时)。语义是"门在预算/环境内复算不了", 不是"发现了断言失败"。

    f-closure-gate-pytest-nonrun-rc-misclassified-blocks-accepted: 旧行为对**任何**非零 rc 一律返
    False, 把 rc=2/3/4/5 (门没拿到真 pass/fail 信号, 如 test_selector 是散文/不存在 nodeid → rc=4/5)
    与真断言失败 rc=1 同判 → verify_closure_against_contract 判 criterion failed 硬拒 →
    confirmed_and_accepted (人接受风险明账) 逃生门失效。区分后: 非-run rc → None → caller (经
    _recompute_criterion) 抬成 GatePytestNotRun → not_recomputable 降级 (镜像 GateGrepTimeout 超时),
    真失败 rc=1 仍返 False 精确拦截 (forbidden_residual: rc=1 绝不洗成 not_recomputable)。

    repo_dir = 该 selector 所属 uv 项目根 (kernel 已用 _resolve_test_target 解析: harness selector →
    harness/; 解耦包 selector → 该包自己的项目根)。`python -m pytest` (非裸 pytest) + 按 _pytest_extra_flags
    决定 --extra —— 让中立解耦包 (pytest 在 optional extra, 独立 .venv) 也能被门真跑 (缺口 a 的执行端)。
    """
    extra_flags = _pytest_extra_flags(repo_dir)
    try:
        proc = subprocess.run(
            ["uv", "run", "--directory", str(repo_dir), *extra_flags,
             "python", "-m", "pytest", "-q", "--no-header", selector],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            cwd=str(repo_dir),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        # 子进程没跑起来 (uv/pytest 缺失 / 超时) = 门没拿到 pass/fail 信号 → None (not_recomputable),
        # 与 GateGrepTimeout 超时同哲学 (镜像): "门复算不了" ≠ "断言失败"。
        return None, f"pytest 跑失败 (门没拿到 pass/fail 信号): {exc!r}"
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-3:]
    detail = f"pytest {selector!r} rc={proc.returncode}: {' / '.join(tail)[:200]}"
    if proc.returncode == 0:
        return True, detail
    if proc.returncode == 1:
        return False, detail
    # rc ∉ {0,1} (中断=2/内部错=3/usage-error=4/no-tests-collected=5/其它): pytest 官方语义 = 门没
    # 拿到真 pass/fail 信号 → None (not_recomputable), 不冒充断言失败。
    return None, detail + " (非-run rc: 门没拿到 pass/fail 信号 → not_recomputable)"


class GatePytestNotRun(Exception):
    """门 pytest 没拿到 pass/fail 信号 (非-run rc / 子进程没跑起来) — 与"真断言失败"和"合约缺陷"都不同判。

    f-closure-gate-pytest-nonrun-rc-misclassified-blocks-accepted: gate_pytest 对非-run 情形返 None
    (见其 docstring)。但 _recompute_criterion 的 None 通道同时承载"合约自相矛盾"(如 TEST 没绑
    test_selector) 这类 **fail-closed** 语义 → 若直接把 gate_pytest 的 None 透传成 _recompute_criterion
    的 None, 会与合约缺陷同判 failed (verify_closure_against_contract L719 collapse)。为把"门没拿到信号"
    这条独立语义送到 not_recomputable 降级路径 (而非 failed), _recompute_criterion 捕 gate_pytest 的
    None 抬成本异常, verify_closure_against_contract 与 GateGrepTimeout 并列捕它 → not_recomputable。
    与 GateGrepTimeout 同型 (超时/非-run 都是"门复算不了", 走降级而非 fail-closed)。
    """


def _git_repo_rel_prefix(repo_dir: Path) -> str:
    """repo_dir 相对其所在 git worktree 顶层的前缀 (如 `harness/`); 算不出/在顶层 → ""。

    worktree 安全: `git show --name-only` 返回的路径相对 worktree 顶层, 这个前缀正是把它们转成
    repo_dir 相对所需剥除的部分。show-prefix 与 name-only 同源同一次 `-C repo_dir` 调用 → 不论
    worktree 落在哪 (主树 / .towow/worktrees / .claude/jobs/...) 都自洽, 不硬编码 `harness/`
    (owner: 前缀问题要根治成高容错)。非 git / 顶层 == repo_dir → "" → 不剥 (旧行为)。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--show-prefix"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _strip_repo_prefix(path: str, prefix: str) -> str:
    """repo-root-relative 路径剥成 repo_dir 相对: prefix 命中则剥, 否则原样 (= repo_dir 外的解耦包)。"""
    if prefix and path.startswith(prefix):
        return path[len(prefix):]
    return path


def gate_git_diff_name_only(repo_dir: Path, commit_shas: list[str]) -> tuple[list[str] | None, str]:
    """门自己 subprocess 跑 git, 取 commit 集的改动文件清单 (去重并集), 返回 (paths, detail).

    返回路径是 **repo_dir 相对** (= harness-relative), 不是 git 仓库根相对 —— 三方法统一基准。
    `git show --name-only` 给的是仓库根相对路径 (harness 子目录场景带 `harness/` 前缀), 而 grep
    的 search_scope / pytest 的 test_selector / git_diff 的 search_scope 前缀过滤都按 repo_dir
    (=harness/) 相对解析; 若不剥前缀, git_diff 的 search_scope 前缀过滤 (p.startswith(scope+"/"))
    会因 `harness/` 前缀全 miss → 作用域内的违禁改动被静默漏放。故这里用 _git_repo_rel_prefix 把
    每条路径剥成 repo_dir 相对 (repo_dir 外的解耦包路径不带该前缀 → 原样保留 repo-root-relative,
    与 _resolve_under_bases 把解耦包按 repo-root-relative 解析同向)。

    paths=None 表示任一 commit 解析失败 (fail-closed — 不许部分清单冒充全清单)。
    空 commit 集 → ([], ...) 诚实空清单 (与 _derive_run_actual_write_set 的 honesty contract 同向:
    run 无 code commit = 改动清单为空, '文件 X 零改动' 类 expected_occurrences=0 自然通过)。
    用 `git show --format= --name-only` (机器算的事实, 非 agent 自报), precedent:
    l0/envelope/builder.derive_actual_write_set_from_commit。
    """
    prefix = _git_repo_rel_prefix(repo_dir)
    seen: set[str] = set()
    order: list[str] = []
    for sha in commit_shas:
        ref = str(sha).strip()
        if not ref:
            return None, f"commit 集含空 sha (fail-closed): {commit_shas!r}"
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_dir), "show", "--no-color", "--format=", "--name-only", ref],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return None, f"git show {ref} 跑失败: {exc!r}"
        if proc.returncode != 0:
            return None, f"git show {ref} 失败 (rc={proc.returncode}): {proc.stderr.strip()[:160]!r}"
        for line in proc.stdout.splitlines():
            p = _strip_repo_prefix(line.strip(), prefix)
            if p and p not in seen:
                seen.add(p)
                order.append(p)
    return order, f"git name-only over {len(commit_shas)} commit(s) → {len(order)} changed path(s)"


# ════════════════════════════════════════════════════════════════════════════════
#  LEDGER_EVENT 门可复算判据 (live-target-execution-evidence@v1 · 唯一真值源)
#  门读 committed canonical 账本、对真实 domain 事件计数 —— goal 收口门 / task 完成门 共用本 kernel。
# ════════════════════════════════════════════════════════════════════════════════


class LedgerScopeContext(BaseModel):
    """gate_ledger_event 的 scope 解析上下文 —— caller 按 machine_check.scope_binding 注入对应锚点。

    advisor 承重指点: this_goal 绝不能退化成 global (否则 goal A 上月合法冻结的 1 条会让 goal B 空声称
    false-accept)。故每个非 global scope 必须有结构锚点; 锚点缺失 → gate_ledger_event 返回 None
    (fail-closed 拒), 永不静默扩成 global。
    """

    model_config = _STRICT

    run_id: str | None = None  # this_run 锚点
    task_id: str | None = None  # this_task 锚点
    # this_goal 锚点: 本 goal 所属 plan 的全 task_id 集 (经 plan→task 结构解析)。事件的
    # provenance.task_id ∈ 此集才计入; 集为 None → 解析失败 → fail-closed 拒。
    goal_task_ids: frozenset[str] | None = None


def _ledger_effective_kind_payload(rec: object) -> tuple[str, dict[str, object]]:
    """(effective_kind, effective_payload) —— 还原 stub-rewrap NodeTouched (镜像 orchestrator._unwrap_stub_rewrap).

    旗舰事件 StrandedBatchManifestFrozen 是 NodeTouched stub-rewrap (event_type=NodeTouched,
    payload.kind="StrandedBatchManifestFrozen", 真字段在 payload.stub_original_payload) —— 裸按
    rec.event_type.value 计数永远 miss (Explore 实证 l2/stranded_batch_manifest.py:44/406)。故计数
    必须先 unwrap。不 import l2.orchestrator (层反转: closure_verification 属 l1), inline 同款逻辑。
    """
    event_type = getattr(rec, "event_type", None)
    et_val = getattr(event_type, "value", event_type)
    raw = getattr(rec, "payload", None)
    payload = raw if isinstance(raw, dict) else {}
    if et_val == "NodeTouched":
        kind = payload.get("kind")
        orig = payload.get("stub_original_payload")
        if isinstance(kind, str) and isinstance(orig, dict):
            return kind, orig
    return str(et_val), payload


def _ledger_payload_matches(payload: dict[str, object], predicate: dict[str, object] | None) -> bool:
    """payload_predicate 每个字段等值匹配 (after_state 嵌套优先, 回退顶层)。None/空 predicate → 恒真。"""
    if not predicate:
        return True
    after = payload.get("after_state")
    after = after if isinstance(after, dict) else {}
    for key, expected in predicate.items():
        actual = after[key] if key in after else payload.get(key)
        if actual != expected:
            return False
    return True


def _ledger_scope_task_id(rec: object, payload: dict[str, object]) -> str | None:
    """一条事件的 task 归属锚点: provenance.task_id 优先, 回退 payload 结构字段 (after_state.task_id / 顶层)。

    team-lead 钦定 (debt-82d4effd33a0 approach a): 真实处置事件把 goal/task 归属"emit 进 provenance
    或 payload 结构字段"。两条 emit 惯例都得认 —— freeze_manifest 的 payload 无 task_id, 归属走 provenance;
    dispose_* (WorktreeClosed/StrandedWorktreeDeletedEmptyShell) 把 task_id 放 payload。两者可信度同级
    (都是已提交账本上的事件, 被审者事后伪造不了)。
    """
    prov = getattr(rec, "provenance", None)
    ptid = getattr(prov, "task_id", None)
    if isinstance(ptid, str) and ptid:
        return ptid
    after = payload.get("after_state")
    if isinstance(after, dict) and isinstance(after.get("task_id"), str) and after["task_id"]:
        return str(after["task_id"])
    top = payload.get("task_id")
    return str(top) if isinstance(top, str) and top else None


def _ledger_scope_run_id(rec: object, payload: dict[str, object]) -> str | None:
    """一条事件的 run 归属锚点: provenance.run_id 优先, 回退 payload 结构字段 (同 task_id 双惯例)。"""
    prov = getattr(rec, "provenance", None)
    prid = getattr(prov, "run_id", None)
    if isinstance(prid, str) and prid:
        return prid
    after = payload.get("after_state")
    if isinstance(after, dict) and isinstance(after.get("run_id"), str) and after["run_id"]:
        return str(after["run_id"])
    top = payload.get("run_id")
    return str(top) if isinstance(top, str) and top else None


def _ledger_in_scope(
    rec: object, payload: dict[str, object], scope: LedgerEventScope, ctx: LedgerScopeContext,
) -> bool:
    """一条事件是否落在 scope 内 (scope 锚点在此前已被 gate_ledger_event 校验为可解析)。

    task/run 归属既认 provenance 又认 payload 结构字段 (见 _ledger_scope_task_id) —— 覆盖 freeze
    (provenance 锚定) 与 dispose_* (payload 锚定) 两类真实处置事件。
    """
    if scope is LedgerEventScope.GLOBAL:
        return True
    if scope is LedgerEventScope.THIS_RUN:
        return _ledger_scope_run_id(rec, payload) == ctx.run_id
    if scope is LedgerEventScope.THIS_TASK:
        return _ledger_scope_task_id(rec, payload) == ctx.task_id
    # THIS_GOAL: 事件 task 归属 ∈ 本 goal 所属 plan 的全 task 集 (结构锚定, 非 global)。
    tid = _ledger_scope_task_id(rec, payload)
    return ctx.goal_task_ids is not None and tid is not None and tid in ctx.goal_task_ids


def gate_ledger_event(
    *,
    event_kind: str,
    payload_predicate: dict[str, object] | None,
    min_occurrences: int,
    scope_binding: LedgerEventScope,
    records: list[object],
    scope: LedgerScopeContext,
) -> tuple[bool | None, str, int]:
    """**唯一真值源**: 门读 committed 账本、对 event_kind 真计数 ≥ min_occurrences → passed。

    返回 (passed, detail, real_count)。passed=None = scope 锚点不可解析 → fail-closed (caller 判拒)。
    passed=False = 真计数 < min (账本证据不足, 拒)。passed=True = 计数达标。

    独立不可伪造: records 是 caller 注入的 **committed canonical 账本** (index.records()), 被审者用
    文字伪造不了 (与 grep/test/git_diff 同质, 读的对象是账本而非仓库树)。计数前先 unwrap stub-rewrap
    (旗舰事件是 NodeTouched 壳)。scope 过滤结构锚定 (advisor #1): this_goal 经 plan→task 集, 绝不
    退化 global。

    诚实边界 (待偿债 debt-ledger-event-count-hot-scan, 已 DebtRegistered): 本 v1 对 caller 注入的
    committed records 线性计数; 该 records = hot committed 流 (index.records() / all_records, Explore
    实证 hot-only)。consolidation/GC 把冷事件归并后, hot 流会漏数真实发生过的事件 → 可能 undercount
    → false reject (fail-closed 安全方向, 非 false accept)。概念§工程决策1 要求终态走 projection
    (l0/projection ProjectionStore.catchup 经 get_events_in_range cold-merging, 计数存活归档) —— 本
    kernel 签名不变、内部计数源日后可无缝换成 projection 读, 调用方零改动 (单一真值源的意义)。
    """
    if min_occurrences < 1:
        return None, f"min_occurrences={min_occurrences} < 1 (合约自相矛盾, fail-closed)", 0
    # scope 锚点可解析性预校验 (advisor #1: 不可解析 → fail-closed 拒, 永不静默扩 global)。
    if scope_binding is LedgerEventScope.THIS_RUN and not scope.run_id:
        return None, "scope=this_run 但 run_id 锚点缺失 — 无法结构锚定, fail-closed", 0
    if scope_binding is LedgerEventScope.THIS_TASK and not scope.task_id:
        return None, "scope=this_task 但 task_id 锚点缺失 — 无法结构锚定, fail-closed", 0
    if scope_binding is LedgerEventScope.THIS_GOAL and scope.goal_task_ids is None:
        return None, (
            "scope=this_goal 但 goal→plan→task 集解析失败 — 无法结构锚定, fail-closed "
            "(绝不静默扩成 global 让别 goal 的旧事件误算)"
        ), 0
    count = 0
    for rec in records:
        kind, payload = _ledger_effective_kind_payload(rec)
        if kind != event_kind:
            continue
        if not _ledger_payload_matches(payload, payload_predicate):
            continue
        if not _ledger_in_scope(rec, payload, scope_binding, scope):
            continue
        count += 1
    passed = count >= min_occurrences
    detail = (
        f"门读 committed 账本 event_kind={event_kind!r} scope={scope_binding.value} "
        f"真计数 {count}, 门槛 ≥{min_occurrences}"
        + (f", predicate={payload_predicate}" if payload_predicate else "")
    )
    return passed, detail, count


# 复算函数签名 (依赖注入便于单测 — 单测注入 fake 跑器, 不真起 subprocess)
GrepRunner = Callable[[str, Path], "tuple[int | None, str]"]
PytestRunner = Callable[[str, Path], "tuple[bool | None, str]"]
# git_diff: caller 把"本任务 commit 集"封进闭包 (合约定 pattern, run 上下文定 commit 集 —
# 合约在 assemble 时不可能知道未来 commit sha, 这正是 T-SL-A1 占位符反模式的根源; 上下文归 caller)。
# 返回 (改动文件清单, detail); None = 无法确定 (fail-closed)。
GitDiffRunner = Callable[[], "tuple[list[str] | None, str]"]
# ledger_event: caller 把 committed 账本 records + scope 上下文封进闭包 (合约定 event_kind/predicate/
# min/scope_binding, 账本与 scope 锚点归 run 上下文)。返回 (passed, detail); None = scope 不可解析
# (fail-closed)。未注入 runner 而合约有 ledger_event criterion → 该条 fail-closed 明拒 (同 git_diff)。
LedgerRunner = Callable[[ClosureCriterion], "tuple[bool | None, str]"]

def _take_first[T](pool: list[T], key: str, attr: str) -> T | None:
    """从消费池里取出第一个 getattr(x, attr)==key 的项 (取走即消费, 防一条 result 顶两条合约项 — V4)."""
    idx = next((i for i, x in enumerate(pool) if getattr(x, attr) == key), None)
    return pool.pop(idx) if idx is not None else None


# ── machine_check 路径解析 (finding-gw1-machine-check-path-defect 缺口 a, 纯路径逻辑无 subprocess) ──
# 背景: 复算 caller (execution work complete / M-1.5 fix-after) 把 repo_dir 钉成 towow.parent
# (= harness/)。harness 内部 task 的 search_scope/test_selector 是 harness-relative (如 tests/cia/x.py),
# 解析对; 但按中立性不变量① 刻意解耦放在 harness/ 之外仓库根的中立配件 (feishu_gateway /
# feishu_owner_channel) 用 repo-root-relative 路径 (如 feishu_gateway/tests) → repo_dir/它 落在
# harness/ 下不存在 → 门 fail-closed 够不着 (execution-level 闭环对解耦包系统性不可达)。
# 修法: caller 把 git 仓库根作 fallback base 传进 extra_bases (git rev-parse 在 caller 算, 不埋进
# 纯 kernel — kernel 靠注入 runner 让单测不起 subprocess); 解析按 (primary=repo_dir, *extra_bases)
# 取第一个 exists 的 → harness 路径仍优先落 harness/ (零行为变化), 解耦包路径落仓库根。两 base 都
# 不存在 → 回 primary/rel (与旧 "search_path 不存在" fail-closed 完全一致, 不掩盖真失败)。


def _resolve_under_bases(rel: str, primary: Path, extra_bases: tuple[Path, ...]) -> Path:
    """rel 相对 (primary, *extra_bases) 逐个试, 返回第一个 exists 的绝对路径; 都不存在 → primary/rel.

    harness-first 顺序保证: 凡在 primary (repo_dir/harness) 下存在的路径解析完全不变 (fallback 只对
    primary 下不存在的路径生效, 不会把已有路径重指到别处) → 零回归。都不存在仍回 primary/rel →
    门按 "search_path 不存在" fail-closed (与旧行为一致)。
    """
    for base in (primary, *extra_bases):
        cand = base / rel
        if cand.exists():
            return cand
    return primary / rel


def _find_project_root(start: Path) -> Path | None:
    """从 start 向上找最近含 pyproject.toml 的目录 = 该路径所属 uv 项目根 (innermost wins)。"""
    cur = start if start.is_dir() else start.parent
    for d in (cur, *cur.parents):
        if (d / "pyproject.toml").exists():
            return d
    return None


def _split_pytest_selector(selector: str) -> tuple[str, str]:
    """把 pytest selector 切成 (文件路径, node-id 后缀含前导 ::)。无 :: → (selector, "")。

    pytest selector 形如 `path/to/test.py::TestClass::test_method` —— 文件路径在第一个 `::` 前,
    其后 (含 `::`) 是 node-id。POSIX 文件路径不含连续双冒号, 故首个 `::` 必是文件/node-id 分界。
    CASE-B 根因: 旧 _resolve_test_target 对整条 selector (含 `::node`) 做 .exists() → 带 node-id
    的 selector 永假 → 落回未纠正 → 带 `harness/` 前缀时 pytest 在错项目根跑 fail-closed BLOCK。
    """
    idx = selector.find("::")
    if idx == -1:
        return selector, ""
    return selector[:idx], selector[idx:]


def _resolve_test_target(
    selector: str, primary: Path, extra_bases: tuple[Path, ...],
) -> tuple[Path, str]:
    """把 test_selector 解析成 (该 selector 所属 uv 项目根, 相对该项目根的 selector)。

    解耦包按中立性是独立 uv 项目 (各自 pyproject + .venv) → 必须在它自己项目根跑 pytest, 不能在
    harness/ 跑 (依赖/路径都错)。解析不到 (selector 在任何 base 下都不存在 / 找不到 pyproject) →
    回 (primary, selector) = 旧行为 → pytest fail-closed。

    CASE-B: 先按首个 `::` 切出文件路径再做 exists/base 解析, 最后把 node-id 后缀重接回纠正后的
    相对 selector —— 否则 `harness/tests/x.py::test_y` 因 `::test_y` 令整条永不 exists, 落回未纠正
    的带前缀 selector → pytest fail-closed。
    """
    file_part, node_suffix = _split_pytest_selector(selector)
    if not file_part:
        return primary, selector
    abs_path = _resolve_under_bases(file_part, primary, extra_bases)
    if not abs_path.exists():
        return primary, selector
    proj = _find_project_root(abs_path)
    if proj is None:
        return primary, selector
    try:
        rel = abs_path.relative_to(proj)
    except ValueError:
        return primary, selector
    return proj, str(rel) + node_suffix


# ════════════════════════════════════════════════════════════════════════════════
#  核心: 三段式 closure 对合约验证
# ════════════════════════════════════════════════════════════════════════════════


def verify_closure_against_contract(
    *,
    contract: ClosureContract,
    result: ClosureResult,
    repo_dir: Path,
    extra_bases: tuple[Path, ...] = (),
    grep_runner: GrepRunner | None = None,
    pytest_runner: PytestRunner | None = None,
    git_diff_runner: GitDiffRunner | None = None,
    ledger_runner: LedgerRunner | None = None,
) -> ClosureVerificationOutcome:
    """三段式 closure 对合约真验证 (T-L1-76 + T-L1-69 + RUN-037 P6 内核).

    段 1 (finding closure): closure_criteria 一对一覆盖 + 门用**合约自带 pattern** 复算。
    段 2 (fix verification): ripple_targets 一对一覆盖 + sync_status。
    段 3 (regression): forbidden_residuals 一对一覆盖 + 门用合约 pattern 自己 grep found=0。

    门复算 (grep/test/git_diff) 用注入的 runner (grep/test 默认真 subprocess)。被审者自报的 passed
    只交叉核对。git_diff_runner 无默认 — "本任务 commit 集"是 run 上下文, 内核无从自取, caller
    不注入而合约有 git_diff criterion → 该条 fail-closed 明拒 (见 _recompute_criterion)。

    extra_bases (finding-gw1-machine-check-path-defect 缺口 a): repo_dir 之外的 fallback base 列表
    (caller 算好 git 仓库根传进, 不在 kernel 起 git subprocess)。grep search_scope / pytest
    test_selector / residual search_scope (合约侧受信字段, 不是 result 侧那个被忽略的 search_path)
    按 (repo_dir, *extra_bases) 解析 — 让按中立性解耦在 harness/
    外的包路径够得着 (default () = 旧行为, 纯路径 exists 检查无 subprocess)。
    """
    grep = grep_runner or gate_grep_occurrences
    pytest_run = pytest_runner or gate_pytest

    verdicts: list[ItemVerdict] = []
    reasons: list[str] = []
    any_failed = False
    any_not_recomputable = False

    # 一对一消费池 (防重复 condition/pattern/artifact 被一条 result 顶替 — RUN-037 advisor V4)。
    crit_pool = list(result.criteria_results)
    ripple_pool = list(result.ripple_results)
    residual_pool = list(result.residual_check_results)

    # ── 段 1: closure_criteria 一对一覆盖 + 门用合约 pattern 复算 ──
    for crit in contract.closure_criteria:
        cond = crit.condition
        cr = _take_first(crit_pool, cond, "criterion")
        if cr is None:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=cond, kind="criterion", status="failed", recomputed_by_gate=False,
                detail="合约 closure_criterion 未被 closure_result 一对一 cover (覆盖性 fail — "
                       "防省略/捏造无关/重复 condition 顶替)",
            ))
            reasons.append(f"criterion 未 cover: {cond[:80]!r}")
            continue
        method = crit.verification_method  # 合约定死的 method (非被审者自报)
        if method not in _GATE_RECOMPUTABLE_CRITERION_METHODS:
            any_not_recomputable = True
            verdicts.append(ItemVerdict(
                item=cond, kind="criterion", status="not_recomputable", recomputed_by_gate=False,
                detail=f"verification_method={method.value} 本质不可门独立复算 → 禁走 resolved 强门 "
                       "(防 manual_reasoning 退化攻击; 待 M-1.5 fix-after cross-check)",
            ))
            reasons.append(f"criterion not_recomputable ({method.value}): {cond[:60]!r}")
            continue
        # GREP / TEST / GIT_DIFF: 门用合约自带 pattern 自己跑, 不信 self_reported_passed
        try:
            gate_passed, detail = _recompute_criterion(
                crit_method=method, crit=crit, repo_dir=repo_dir, extra_bases=extra_bases,
                grep=grep, pytest_run=pytest_run, git_diff_runner=git_diff_runner,
                ledger_runner=ledger_runner,
            )
        except GateGrepTimeout as exc:
            # 超时 ≠ 真 fail: 门预算内复算不了 → not_recomputable 降级 (不进 resolved 强门,
            # 也不把"没算完"冒充"criterion 未满足")。
            any_not_recomputable = True
            verdicts.append(ItemVerdict(
                item=cond, kind="criterion", status="not_recomputable", recomputed_by_gate=False,
                detail=f"门 grep 超时预算内没扫完 (扫描面过大, ≠criterion 未满足) → 降级: {exc}",
            ))
            reasons.append(f"criterion grep 超时 (not_recomputable): {cond[:60]!r}")
            continue
        except GatePytestNotRun as exc:
            # 门 pytest 没拿到 pass/fail 信号 (非-run rc: 中断/内部错/usage-error/no-tests-collected,
            # 或子进程没跑起来) ≠ 真断言失败: → not_recomputable 降级 (镜像 GateGrepTimeout)。这使含
            # 不可机器执行 test_selector 的合约不再被硬判 failed, confirmed_and_accepted 逃生门恢复
            # (f-closure-gate-pytest-nonrun-rc-misclassified-blocks-accepted)。
            any_not_recomputable = True
            verdicts.append(ItemVerdict(
                item=cond, kind="criterion", status="not_recomputable", recomputed_by_gate=False,
                detail=f"门 pytest 没拿到 pass/fail 信号 (非-run rc, ≠断言失败) → 降级: {exc}",
            ))
            reasons.append(f"criterion pytest 非-run rc (not_recomputable): {cond[:60]!r}")
            continue
        if gate_passed is None:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=cond, kind="criterion", status="failed", recomputed_by_gate=True,
                detail=f"门复算无法执行 (fail-closed): {detail}",
            ))
            reasons.append(f"criterion 复算失败: {cond[:60]!r} — {detail}")
            continue
        if gate_passed != cr.self_reported_passed:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=cond, kind="criterion", status="failed", recomputed_by_gate=True,
                detail=f"门复算 ({gate_passed}) 与自报 ({cr.self_reported_passed}) 矛盾 — 抓撒谎拒。{detail}",
            ))
            reasons.append(f"criterion 复算与自报矛盾: {cond[:60]!r}")
            continue
        if not gate_passed:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=cond, kind="criterion", status="failed", recomputed_by_gate=True,
                detail=f"门复算 criterion 未满足: {detail}",
            ))
            reasons.append(f"criterion 复算 fail: {cond[:60]!r}")
            continue
        verdicts.append(ItemVerdict(
            item=cond, kind="criterion", status="passed", recomputed_by_gate=True,
            detail=f"门复算通过 (合约 pattern): {detail}",
        ))

    # ── 段 2: ripple_targets 一对一覆盖 + sync_status ──
    for rt in contract.ripple_targets:
        art = rt.artifact
        rr = _take_first(ripple_pool, art, "artifact")
        if rr is None:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=art, kind="ripple", status="failed", recomputed_by_gate=False,
                detail="合约 ripple_target 未被 closure_result 一对一 cover (覆盖性 fail)",
            ))
            reasons.append(f"ripple 未 cover: {art!r}")
            continue
        if rr.sync_status is ClosureRippleSyncStatus.PENDING:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=art, kind="ripple", status="failed", recomputed_by_gate=False,
                detail="ripple sync_status=pending — 未同步 (拒)",
            ))
            reasons.append(f"ripple pending: {art!r}")
            continue
        if rr.sync_status is ClosureRippleSyncStatus.NOT_APPLICABLE and not (rr.evidence and rr.evidence.strip()):
            any_failed = True
            verdicts.append(ItemVerdict(
                item=art, kind="ripple", status="failed", recomputed_by_gate=False,
                detail="ripple sync_status=not_applicable 但缺 evidence (拒)",
            ))
            reasons.append(f"ripple not_applicable 无 evidence: {art!r}")
            continue
        verdicts.append(ItemVerdict(
            item=art, kind="ripple", status="passed", recomputed_by_gate=False,
            detail=f"ripple sync_status={rr.sync_status.value}",
        ))

    # ── 段 3: forbidden_residuals 一对一覆盖 + 门用合约 pattern 自己 grep ──
    for fr in contract.forbidden_residuals:
        pat = fr.pattern
        rcr = _take_first(residual_pool, pat, "pattern")
        if rcr is None:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=pat, kind="residual", status="failed", recomputed_by_gate=False,
                detail="合约 forbidden_residual 未被 closure_result 一对一 cover (覆盖性 fail — T-L1-53 省略探针)",
            ))
            reasons.append(f"residual 未 cover: {pat!r}")
            continue
        if fr.check_method not in _GATE_RECOMPUTABLE_RESIDUAL_METHODS:
            any_not_recomputable = True
            verdicts.append(ItemVerdict(
                item=pat, kind="residual", status="not_recomputable", recomputed_by_gate=False,
                detail=f"check_method={fr.check_method.value} 门不可独立复算 → 禁走 resolved 强门 (降级)",
            ))
            reasons.append(f"residual not_recomputable ({fr.check_method.value}): {pat!r}")
            continue
        # GREP: 门用合约自带 pattern (被审者不能换) 自己跑
        # scope 优先用 reviewer 合约设的 fr.search_scope (trusted); None → 全域。
        # 忽略 rcr.search_path (被审者自报, 可指向空目录永远 pass — gaming hole)。
        if fr.search_scope is not None:
            scope_defect = search_scope_shape_defect(fr.search_scope)
            if scope_defect:
                any_failed = True
                verdicts.append(ItemVerdict(
                    item=pat, kind="residual", status="failed", recomputed_by_gate=False,
                    detail=f"合约 forbidden_residual search_scope 非路径形态 — 合约缺陷, fail-closed: {scope_defect}",
                ))
                reasons.append(f"residual search_scope 非路径形态: {pat!r}")
                continue
            scope = _resolve_under_bases(fr.search_scope, repo_dir, extra_bases)
        else:
            scope = repo_dir
        try:
            occ, detail = grep(pat, scope)
        except GateGrepTimeout as exc:
            # 超时 ≠ 真残留: 门预算内没扫完 → not_recomputable 降级, 不判 failed
            # (f-closure-residual-unscoped-grep-backup-variant-timeout-false-positive ③:
            # 旧行为 TimeoutExpired→None→failed 与"有残留"同判, 真闭合拿不到机器凭证)。
            any_not_recomputable = True
            verdicts.append(ItemVerdict(
                item=pat, kind="residual", status="not_recomputable", recomputed_by_gate=False,
                detail=f"门 grep 超时预算内没扫完 (扫描面过大, ≠真残留) → 降级: {exc}",
            ))
            reasons.append(f"residual grep 超时 (not_recomputable): {pat!r}")
            continue
        if occ is None:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=pat, kind="residual", status="failed", recomputed_by_gate=True,
                detail=f"门 grep 无法执行 (fail-closed): {detail}",
            ))
            reasons.append(f"residual grep 失败: {pat!r}")
            continue
        if occ > 0:
            any_failed = True
            verdicts.append(ItemVerdict(
                item=pat, kind="residual", status="failed", recomputed_by_gate=True,
                detail=f"forbidden_residual 残留 {occ} occurrences — 没修干净 (拒)。{detail}",
            ))
            reasons.append(f"residual 残留非 0: {pat!r} ({occ})")
            continue
        verdicts.append(ItemVerdict(
            item=pat, kind="residual", status="passed", recomputed_by_gate=True,
            detail=f"门 grep found=0: {detail}",
        ))

    strong_gate_passed = (not any_failed) and (not any_not_recomputable)
    downgrade_required = (not any_failed) and any_not_recomputable
    if any_not_recomputable and not any_failed:
        reasons.append(
            "含 not_recomputable 项 (门不可独立复算的 verification_method/check_method) → "
            "resolved 强门不通过, 应降级 partial 并由 M-1.5 fix-after cross-check 补验",
        )
    return ClosureVerificationOutcome(
        strong_gate_passed=strong_gate_passed,
        downgrade_required=downgrade_required,
        verdicts=verdicts,
        reasons=reasons,
    )


def derive_closure_state(
    outcome: ClosureVerificationOutcome,
    *,
    has_unrelated_findings: bool = False,
) -> ReviewClosureState:
    """M-1.5 §7.3 路径 B — 从门复算 verdicts 派生 closure_state (fix-after bounded closure).

    派生规则 (§7.3 的 4 步映射, 不信被审者自报的 closure_state, 由门 verdict 钦定):
      - 段 1 任一 closure_criterion failed → **fix_insufficient** (原 finding 没修好; 优先级最高 —
        criterion 是 finding 核心修复条件, 没满足谈不上 ripple)。
      - 段 2/3 任一 ripple failed (未 synced) 或 residual failed (found>0) → **ripple_incomplete**
        (criteria 满足但涟漪未全同步 / 有残留)。
      - 全 passed 且无 unrelated → **closed** (终态)。
      - 全 passed 且 scope 外发现新问题 (caller 声明 has_unrelated_findings) →
        **new_unrelated_finding_logged** (当前 finding 闭合, 新 finding 进 backlog 不阻塞)。

    含 not_recomputable 项 (门不可独立复算) → 不能声称 closed → 退回 fix_insufficient
    (强门不通过该 reopen / 走 cross-check, 防 manual_reasoning 退化攻击钻进终态)。
    """
    criterion_failed = any(
        v.kind == "criterion" and v.status == "failed" for v in outcome.verdicts
    )
    ripple_or_residual_failed = any(
        v.kind in ("ripple", "residual") and v.status == "failed" for v in outcome.verdicts
    )
    if criterion_failed:
        return ReviewClosureState.FIX_INSUFFICIENT
    if outcome.downgrade_required:
        # not_recomputable criterion 不可机器验闭合 → 不进终态, 当 fix_insufficient (reopen / cross-check)
        return ReviewClosureState.FIX_INSUFFICIENT
    if ripple_or_residual_failed:
        return ReviewClosureState.RIPPLE_INCOMPLETE
    if has_unrelated_findings:
        return ReviewClosureState.NEW_UNRELATED_FINDING_LOGGED
    return ReviewClosureState.CLOSED


def _self_reported_found(residual: dict[str, object]) -> int:
    """读 degraded 自报 residual 的 found_occurrences (容错 int/str/None, bool 不当数字)."""
    raw = residual.get("found_occurrences", 0)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return 0
    return 0


def derive_degraded_closure_state(
    cv: dict[str, object],
    *,
    has_unrelated_findings: bool = False,
) -> ReviewClosureState:
    """无 closure_contract 的系统 finding 的 fix-after 闭合 — 自洽底线派生 (degraded path).

    背景: 执行 / maintenance 侧顺手上报的 finding 无 closure_contract (work 链 finding-create 不强制),
    占 finding 总量相当比例。这类 finding 走 fix-after 闭环时门**无合约 pattern 可独立 grep**, 只能读
    被审者自报。本函数从自报 (criteria passed / ripple sync_status / residual found_occurrences) 派生
    closure_state, 与 derive_closure_state 同优先级映射 (criterion 失败 > ripple/residual > unrelated >
    closed), 但判据是自报非门复算 —— 故**弱**, 调用方须在其上叠加独立 verify-step falsifier fork
    (finding-fixafter-degraded-no-independent-fork-1: 自报自洽不能是这类 finding 闭合的唯一防线)。

    形状: cv 是 finding ClosureVerification 原始形状 (criteria_results=[{criterion,passed,evidence}],
    ripple_results=[{...,sync_status}], residual_check_results=[{pattern,found_occurrences}]), 非
    ClosureResult (后者门用合约复算; 此处无合约故直读自报)。
    """
    crits_raw = cv.get("criteria_results")
    crits = crits_raw if isinstance(crits_raw, list) else []
    # 空 criteria / 任一自报未 passed → 谈不上闭合 (criterion 优先级最高, 对齐 derive_closure_state)。
    if not crits or any(not (isinstance(c, dict) and c.get("passed") is True) for c in crits):
        return ReviewClosureState.FIX_INSUFFICIENT
    ripples_raw = cv.get("ripple_results")
    ripples = ripples_raw if isinstance(ripples_raw, list) else []
    if any(
        isinstance(r, dict) and r.get("sync_status") not in ("synced", "not_applicable")
        for r in ripples
    ):
        return ReviewClosureState.RIPPLE_INCOMPLETE
    residuals_raw = cv.get("residual_check_results")
    residuals = residuals_raw if isinstance(residuals_raw, list) else []
    if any(isinstance(r, dict) and _self_reported_found(r) > 0 for r in residuals):
        return ReviewClosureState.RIPPLE_INCOMPLETE
    if has_unrelated_findings:
        return ReviewClosureState.NEW_UNRELATED_FINDING_LOGGED
    return ReviewClosureState.CLOSED


def _occurrence_passes(occ: int, expected: int, comparator: OccurrenceComparator | None) -> bool:
    """GREP/GIT_DIFF 复算比较 (finding f-ohr3-done-criteria-exact-match-vs-existence-intent 修复).

    comparator=None (存量判据, 未显式声明) → EQ, 与修复前 `occ == expected` 行为逐位不变 (重放安全)。
    GTE → 存在性下限 `occ >= expected`, 只在合约显式声明时启用 — 精确计数不变量 (如"残留必须恰好 0
    处") 与存在性不变量 (如"至少引用 1 处") 从此各自有对应语义, 不再共用一把只能表达"精确相等"的尺子。
    """
    if comparator is OccurrenceComparator.GTE:
        return occ >= expected
    return occ == expected


def _recompute_criterion(
    *,
    crit_method: ClosureVerificationMethod,
    crit: ClosureCriterion,
    repo_dir: Path,
    extra_bases: tuple[Path, ...] = (),
    grep: GrepRunner,
    pytest_run: PytestRunner,
    git_diff_runner: GitDiffRunner | None = None,
    ledger_runner: LedgerRunner | None = None,
) -> tuple[bool | None, str]:
    """门对一条 GREP/TEST/GIT_DIFF criterion 用**合约自带 spec** 复算, 返回 (gate_passed, detail).

    None=无法执行 → caller 判 failed (fail-closed)。合约 grep/git_diff 没绑 verification_pattern/
    expected_occurrences, 或 test 没绑 test_selector = 合约自相矛盾 (声明可机器验却不给参数) →
    返回 None → fail-closed (RUN-037 advisor V1: 非降级 — 降级只配本质不可机器验的方法)。
    search_scope 非路径形态 (占位符/命令文本/绝对路径/越界 — T-SL-A1 反模式) → 同样 fail-closed
    明拒 (finding-machine-check-encoding-gap-1780563112 缺口 b; 发布门有同源校验, 这里是复算侧防线
    — closure_contract 不走发布门, 须独立设防)。
    """
    if crit.search_scope is not None:
        scope_defect = search_scope_shape_defect(crit.search_scope)
        if scope_defect:
            return None, (
                f"合约 search_scope 非路径形态 — 合约缺陷该退回 assemble/reviewer 补, fail-closed: "
                f"{scope_defect}"
            )
    if crit_method is ClosureVerificationMethod.GREP:
        if not crit.verification_pattern or crit.expected_occurrences is None:
            return None, ("合约 GREP criterion 未绑 verification_pattern/expected_occurrences — "
                          "合约自相矛盾 (声明 grep 验却没给 pattern), fail-closed")
        # search_scope 按 (repo_dir, *extra_bases) 解析 (缺口 a: 解耦包目录在 harness/ 外仍够得着)。
        scope = (
            _resolve_under_bases(crit.search_scope, repo_dir, extra_bases)
            if crit.search_scope else repo_dir
        )
        occ, detail = grep(crit.verification_pattern, scope)
        if occ is None:
            return None, detail
        cmp_symbol = ">=" if crit.occurrence_comparator is OccurrenceComparator.GTE else "=="
        return (_occurrence_passes(occ, crit.expected_occurrences, crit.occurrence_comparator)), (
            f"门 grep 合约 pattern {crit.verification_pattern!r} 得 {occ}, "
            f"期望 {cmp_symbol} {crit.expected_occurrences}. {detail}"
        )
    if crit_method is ClosureVerificationMethod.TEST:
        if not crit.test_selector:
            return None, "合约 TEST criterion 未绑 test_selector — 合约自相矛盾, fail-closed"
        # selector 解析到它自己的 uv 项目根 (缺口 a: 解耦包是独立 uv 项目, 须在各自项目根跑 pytest;
        # harness selector 仍落 harness/ 零变化)。pytest runner 在该项目根跑 (gate_pytest 自探 --extra)。
        project_root, rel_selector = _resolve_test_target(crit.test_selector, repo_dir, extra_bases)
        passed, detail = pytest_run(rel_selector, project_root)
        if passed is None:
            # gate_pytest 的 None = 门没拿到 pass/fail 信号 (非-run rc / 子进程没跑起来) —— **不是**
            # 合约缺陷 (那条 fail-closed 是上面 `if not crit.test_selector` 的 return None)。抬成
            # GatePytestNotRun 让 verify_closure_against_contract 判 not_recomputable 降级 (镜像
            # GateGrepTimeout), 而非与合约缺陷同判 failed
            # (f-closure-gate-pytest-nonrun-rc-misclassified-blocks-accepted)。
            raise GatePytestNotRun(detail)
        return passed, detail
    if crit_method is ClosureVerificationMethod.GIT_DIFF:
        if not crit.verification_pattern or crit.expected_occurrences is None:
            return None, ("合约 GIT_DIFF criterion 未绑 verification_pattern/expected_occurrences — "
                          "合约自相矛盾 (声明改动清单验却没给 pattern), fail-closed")
        if git_diff_runner is None:
            return None, ("GIT_DIFF criterion 需要本任务 commit 集上下文, caller 未注入 "
                          "git_diff_runner — fail-closed (此验证路径未接线, 不静默降级; "
                          "M-1.4 work complete 已接线)")
        try:
            pattern = re.compile(crit.verification_pattern)
        except re.error as exc:
            return None, f"合约 GIT_DIFF verification_pattern 非法正则 ({exc}) — fail-closed"
        paths, detail = git_diff_runner()
        if paths is None:
            return None, detail
        if crit.search_scope:
            prefix = crit.search_scope.rstrip("/")
            paths = [p for p in paths if p == prefix or p.startswith(prefix + "/")]
        hits = [p for p in paths if pattern.search(p)]
        cmp_symbol = ">=" if crit.occurrence_comparator is OccurrenceComparator.GTE else "=="
        return (_occurrence_passes(len(hits), crit.expected_occurrences, crit.occurrence_comparator)), (
            f"门 git_diff 改动清单比对: pattern {crit.verification_pattern!r} 命中 {len(hits)} "
            f"(期望 {cmp_symbol} {crit.expected_occurrences}; 清单 {len(paths)} 路径"
            f"{', scope=' + crit.search_scope if crit.search_scope else ''}). {detail}"
            + (f" 命中: {hits[:5]}" if hits else "")
        )
    if crit_method is ClosureVerificationMethod.LEDGER_EVENT:
        if not crit.event_kind or crit.min_occurrences is None or crit.scope_binding is None:
            return None, ("合约 LEDGER_EVENT criterion 未绑 event_kind/min_occurrences/scope_binding — "
                          "合约自相矛盾 (声明读账本计数却没给参数), fail-closed")
        if ledger_runner is None:
            return None, ("LEDGER_EVENT criterion 需要 committed 账本 + scope 上下文, caller 未注入 "
                          "ledger_runner — fail-closed (此验证路径未接线, 不静默降级)")
        return ledger_runner(crit)
    return None, f"未预期的可复算 method: {crit_method.value}"


__all__ = [
    "ClosureResult",
    "ClosureVerificationOutcome",
    "CriterionResult",
    "GateGrepTimeout",
    "GatePytestNotRun",
    "GitDiffRunner",
    "ItemVerdict",
    "LedgerRunner",
    "LedgerScopeContext",
    "ResidualCheckResult",
    "RippleResult",
    "derive_closure_state",
    "derive_degraded_closure_state",
    "gate_git_diff_name_only",
    "gate_grep_occurrences",
    "gate_ledger_event",
    "gate_pytest",
    "verify_closure_against_contract",
]
