"""模块对账卡 — 活标准 (T-LS build).

# spec source: 01-reconciliation/LIVING-STANDARD-BUILD-PLAN.md
# Owner 2026-06-05: 把全量审计变成会自检过期 + 自动喂 agent 的活对象。

这是 owner 说的「卡片」：每模块一张，三行——该干嘛 / 接没接通 / 上次核哪天 ——
外加**红点**：模块代码/spec 在「上次核」之后动过 → 这张卡自动标 stale (🔴 需重核)。
红点是这套东西区别于「又一份会烂掉的文档」的唯一关键：它能自己发现自己过期。

T-LS-2 (本模块): staleness 红点纯逻辑 + 读卡。数据暂从审计派生的 cards.json 读;
T-LS-1 完成后源切到 capability board (单一真相源), 本模块的 staleness 逻辑不变。
"""

from __future__ import annotations

import json
import re
import subprocess
import tokenize
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord


def load_cards(cards_file: Path) -> list[dict[str, Any]]:
    """读模块对账卡 (list[dict[str, Any]])。每卡含 module_id/code_paths/spec_path/verified_date 等。"""
    data = json.loads(cards_file.read_text(encoding="utf-8"))
    return data.get("cards", []) if isinstance(data, dict) else list(data)


def _git_last_change(repo: Path, path: str) -> str:
    """某路径最近一次 commit 日期 (YYYY-MM-DD)；查不到返回空串。"""
    # git by-PATH, list-args + shell=False (同 cli/main.py per-file precedent) → S603/S607 intentional
    r = subprocess.run(  # noqa: S603
        ["git", "log", "-1", "--format=%cd", "--date=short", "--", path],  # noqa: S607
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    return r.stdout.strip()


def code_last_changed(repo: Path, paths: list[str]) -> str:
    """这张卡所有代码/spec 路径里, 最新的一次改动日期 (取 max)。"""
    latest = ""
    for p in paths:
        d = _git_last_change(repo, p)
        if d > latest:
            latest = d
    return latest


def card_staleness(card: dict[str, Any], repo: Path) -> dict[str, Any]:
    """红点判定: 模块代码/spec 在 verified_date 之后动过 → stale=True。

    返回 {code_last_changed, verified_date, stale}。
    这就是演示里那根「代码一改冒红点」的线, 落成可复用纯逻辑。
    """
    paths = list(card.get("code_paths", []))
    spec = card.get("spec_path")
    if spec:
        paths = [*paths, spec]
    changed = code_last_changed(repo, paths)
    verified = card.get("verified_date", "")
    stale = bool(changed) and bool(verified) and changed > verified
    return {"code_last_changed": changed, "verified_date": verified, "stale": stale}


# ── T-LS-1: 模块 built/enforced 从能力板 rollup 派生 (板子=单一真相源) ──────────────────
#
# cards.json 里写死的 built/enforced 是 2026-06-05 审计当天的快照, 会跟板子分叉。
# 这里改成从 capability_status 投影 (611 条能力, event-sourced) 实时 rollup, 让模块卡
# 的"接没接通"永远反映板子今天的真相, 不留第二份会烂掉的数。verified_date / code_paths /
# spec_path / should_do 这些审计派生的静态部分仍来自 cards.json。


def _advance_event_date(event_log: EventLog, event_id: str | None) -> str:
    """某 advance 事件的日期 (YYYY-MM-DD); 查不到返回空串。

    这是模块 last_verified 的真来源: 板子上一条能力被 advance 的事件时间, 而不是审计
    当天写死的 cards.json verified_date。
    """
    if not event_id:
        return ""
    try:
        ev = event_log.get_event(event_id)
    except Exception:  # 损坏/缺失事件不该让整个 rollup 崩, 退回空串
        return ""
    if ev is None:
        return ""
    ts = getattr(ev, "timestamp", None)
    if ts is None:
        return ""
    try:
        return str(ts.date().isoformat())
    except Exception:
        return str(ts)[:10]


def module_rollup_from_board(towow_dir: Path) -> dict[str, dict[str, Any]]:
    """从 capability_status 板子按模块 rollup 出 built/enforced + last_verified。

    返回 {module_id: {"enforced": "enforced"|"built_not_enforced", "last_verified": "YYYY-MM-DD"|"",
    "n_caps": int, "n_built": int, "n_enforced": int}}。

    rollup 规则 (fail-closed): 模块判 'enforced' 当且仅当它**每一条**能力都有正向 enforced 证据
    (enforcement_level == 'enforced')，即 n_enforced == n_caps 且 n_caps > 0；任一能力是 'built'
    (建好没接通) 或**缺标记** (None/未 advance) → 整模块塌成 built_not_enforced。

    这是 finding f-glob-capability-board-reports-false-enforced (CAP-01/CAP-02, major) 的根治:
    旧规则 'n_built > 0 → bne，否则 enforced' 默认极性反了 —— 靠"没人标 built"(即没人说它没接通)
    就推 enforced，把 6 个审计认定 built_not_enforced 的模块全报成 enforced。状态板是 owner 据以
    判"能不能开机"的可信监督核心 (@new-capability-task-classifier / @capability-done-real-via-
    production-path)，高报 enforced = 让决策建在假状态上。fail-closed 偏严: 没有正向 enforced 证据
    一律不冒充 enforced (built≠enforced)，宁可保守报 built_not_enforced。

    last_verified = 该模块所有能力里最近一次 advance 事件的日期 (无 advance 则空串, 调用方退回
    cards.json 的审计 verified_date)。

    板子缺失/空 → 返回空 dict, 调用方退回 cards.json 写死的 built/enforced (graceful fallback)。
    """
    from towow.l0.event_log import EventLog
    from towow.l0.projection import ProjectionStore

    graph_dir = towow_dir / "graph"
    event_log = EventLog(towow_dir / "events.log")
    store = ProjectionStore(graph_dir)
    # read-triggers-catch-up: 先把板子追到最新 committed 状态再读 (M-0.2 契约)。
    store.catchup(event_log)
    proj = store.read("capability_status")
    if not isinstance(proj, dict):
        return {}
    caps = proj.get("capabilities", [])
    if not isinstance(caps, list) or not caps:
        return {}

    # 每模块累计 built/enforced 计数 + 收集 advance 日期取最近。
    acc: dict[str, dict[str, Any]] = {}
    for c in caps:
        if not isinstance(c, dict):
            continue
        mod = c.get("module")
        if not isinstance(mod, str) or not mod:
            continue
        slot = acc.setdefault(
            mod,
            {"n_caps": 0, "n_built": 0, "n_enforced": 0, "_advance_ids": []},
        )
        slot["n_caps"] += 1
        enf = c.get("enforcement_level")
        if enf == "built":
            slot["n_built"] += 1
        elif enf == "enforced":
            slot["n_enforced"] += 1
        adv_id = c.get("advanced_by_event_id")
        if isinstance(adv_id, str) and adv_id:
            slot["_advance_ids"].append(adv_id)

    result: dict[str, dict[str, Any]] = {}
    for mod, slot in acc.items():
        # fail-closed: 只在**每条**能力都有正向 enforced 证据时才判 enforced
        # (n_enforced == n_caps 且 n_caps > 0); 任一 'built' 或缺标记 → built_not_enforced。
        # 绝不靠"没人标 built"反推 enforced (那是 f-glob-capability-board-reports-false-enforced 的根因)。
        rolled = "enforced" if (slot["n_caps"] > 0 and slot["n_enforced"] == slot["n_caps"]) else "built_not_enforced"
        last_verified = ""
        for adv_id in slot["_advance_ids"]:
            d = _advance_event_date(event_log, adv_id)
            if d > last_verified:
                last_verified = d
        result[mod] = {
            "enforced": rolled,
            "last_verified": last_verified,
            "n_caps": slot["n_caps"],
            "n_built": slot["n_built"],
            "n_enforced": slot["n_enforced"],
        }
    return result


# ── T-LS-4: owner 可读卡视图 (按层分组的 20 卡全貌 + 汇总) ────────────────────────────────
#
# owner 不读代码也不读 611 条能力板, 她要的是 CONFORMANCE-MAP 那张表的活版本: 按层 (L0/
# L1/L2/L3) 分组, 每模块一行 — 名字 / 红点 / built / enforced 或 built-not-enforced / 缺口
# 一类 / 该干嘛一句, 底下一行汇总。built/enforced 走 T-LS-1 板子派生 (单一真相源), 红点走
# card_staleness (代码改过自动冒红)。这里只做"把卡组织成视图行"的纯逻辑, 渲染 (颜色/对齐/
# echo) 留在 CLI, 这样视图结构可独立测、不依赖终端。

# 层顺序 (CONFORMANCE-MAP 的读法: L0 横向基础设施 → L1 阶段 skill → L2 维护层 → L3 外壳)。
LAYER_ORDER = ["L0", "L1", "L2", "L3"]

# 层一句话标题 (owner 视角的"这层是干嘛的")。
LAYER_TITLE = {
    "L0": "L0 横向运行时基础设施 (事件日志/投影/capsule/envelope/门)",
    "L1": "L1 阶段 skill (采访→共识→计划→执行→复查→修复)",
    "L2": "L2 维护层 (变更影响/返工daemon/escalation)",
    "L3": "L3 工程外壳 (CLI/UI投影/迁移/验证)",
}

# 缺口类型 → owner 能懂的一句。审计的三类: 一致 / 设计细但落地坏断开 / spec 自己有洞。
# 注意: 缺口标记**不用 🔴** —— 🔴 在本视图专留给"过期红点"(staleness)。缺口型用 ⛔/🟠
# 区分, 否则 type1 缺口的行会跟"代码改过需重核"的红点撞色 (owner 实测看着像一堆红点)。
GAP_LABEL = {
    "none": "✅ 一致",
    "type1_designed_but_broken_or_disconnected": "⛔ 设计细但落地坏/断开",
    "type3_spec_itself_has_hole": "🟠 spec 自己有洞",
}

# enforced 维度 → owner 能懂的一句。enforced=真接进运行链; built_not_enforced=建好没接通。
ENFORCED_LABEL = {
    "enforced": "✅ enforced 接进运行链",
    "built_not_enforced": "🟡 built 没接通",
}


def _first_sentence(text: str, limit: int = 60) -> str:
    """should_do 取第一句 (到第一个句号/分号止), 再裁到 limit, 给 owner 一句话不刷屏。"""
    if not text:
        return ""
    s = str(text)
    for sep in ("。", ";", "；", "\n"):
        idx = s.find(sep)
        if idx != -1:
            s = s[:idx]
            break
    s = s.strip()
    if len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def build_view_rows(
    cards: list[dict[str, Any]],
    board: dict[str, dict[str, Any]],
    staleness_of: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """把每张卡组织成一条 owner 可读视图行 (built/enforced 走板子, 红点走 staleness)。

    cards: load_cards 的产物。board: module_rollup_from_board 的产物 ({} 则退回卡静态值)。
    staleness_of: callable(card) -> {"stale": bool, ...} (注入 card_staleness, 便于测试不碰 git)。

    返回每行 dict: module_id/name/layer/stale/built/enforced/enforced_value/gap_type/should_do。
    enforced 维度优先从板子 rollup 取 (单一真相源), 板子无此模块则退回卡里写死的值。
    """
    rows: list[dict[str, Any]] = []
    for card in cards:
        mid = str(card.get("module_id", "?"))
        s = staleness_of(card)
        roll = board.get(mid)
        enf_value = roll["enforced"] if roll else card.get("enforced")
        rows.append(
            {
                "module_id": mid,
                "name": str(card.get("name", "")),
                "layer": str(card.get("layer", "")),
                "stale": bool(s.get("stale")),
                "built": str(card.get("built", "")),
                "enforced_value": enf_value,
                "gap_type": str(card.get("gap_type", "")),
                "should_do": _first_sentence(str(card.get("should_do", ""))),
            }
        )
    return rows


def group_rows_by_layer(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """把视图行按层 (L0→L1→L2→L3) 分组, 层内按 module_id 排序。

    返回 [(layer, [rows...]), ...]。已知层按 LAYER_ORDER, 出现的未知层追加在后 (gapless:
    任何卡的层都不会被悄悄丢掉)。
    """
    by_layer: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_layer.setdefault(r["layer"], []).append(r)
    ordered_layers = [layer for layer in LAYER_ORDER if layer in by_layer]
    ordered_layers += [layer for layer in by_layer if layer not in LAYER_ORDER]
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for layer in ordered_layers:
        out.append((layer, sorted(by_layer[layer], key=lambda r: r["module_id"])))
    return out


def view_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总: enforced 几个 / built-not-enforced 几个 / 红点几个 / 共几张。"""
    enforced = sum(1 for r in rows if r["enforced_value"] == "enforced")
    not_enforced = sum(1 for r in rows if r["enforced_value"] == "built_not_enforced")
    stale = sum(1 for r in rows if r["stale"])
    return {
        "total": len(rows),
        "enforced": enforced,
        "built_not_enforced": not_enforced,
        "stale": stale,
    }


# ── T-LRG-B1-guardtest: 账本读路径静态护栏·层2 守护测试的共享真源 ────────────────────────
#
# concept: ledger-read-static-guardrail@v1 / ledger-read-path-invariant@v1（批1冻结快照）。
#
# 这一段是护栏**层2**（守护测试）的可复用真源。层1 是 PreToolUse hook（写入瞬间物理拒新增
# 无豁免的全量读调用，T-LRG-B1-hook 落地）；层2 是守护测试，兜住"用 bash 直写（heredoc /
# echo>file）绕过 Write/Edit hook"这条路——不管调用是怎么写进盘上文件的，扫盘上真实源码即可
# 抓到。两层判定必须用**同一条豁免正则**（否则两层漂移 = 消费方歧义）。
#
# 【单一真源，不各写一份】豁免正则 LEDGER_SCAN_OK_RE 定义在这里一处；守护测试 import 它、
# 不重抄。层1 hook 与 B6 门面平移届时同样收敛到这一条（跨层字节一致由 T-LRG-B1-gate 复核，
# 本工位隔离下 hook 的真源尚未合入，故不在运行时对照 hook 文件——那会在本工位必红且属 gate 职责）。
#
# 【为何锚复杂度不锚函数名·载体特定】v1 静态拦截只认公开符号 all_records() 的调用（owner
# nj-9367a728）；无界 get_events_in_range 不进 v1 静态拦截。批6 载体从行尾注释平移为门面
# API 声明参数时，本段随之演进——但豁免的三要素语义（reason/transcript/approved）不变。

# 豁免标记语法（行尾注释，锚到确切调用行、代码搬动不漂移，同 flake8 noqa 惯例）：
#   x.all_records()  # ledger-scan-ok: <reason> | transcript=<session_id> | approved=<approval_event_id>
# 三字段全必填：reason 非空自然语言理由；transcript 可追溯到写下豁免的会话标识；approved 指向
# canonical 审批落账事件 id。正则与 ledger-read-static-guardrail@v1 冻结定义逐字节一致。
LEDGER_SCAN_OK_RE = re.compile(
    r"#\s*ledger-scan-ok:\s*(?P<reason>.+?)\s*\|\s*transcript=(?P<anchor>\S+)\s*\|\s*approved=(?P<approval>\S+)"
)

# 被拦的全量读符号（v1 只认这一个公开符号；def 定义点不算调用点，天然不被 `.<name>(` 匹配）。
LEDGER_GUARDED_SYMBOL = "all_records"

# 合法审批载体的 canonical 事件类型（owner-confirm / escalation 通道产物）。approved= 锚必须
# 解析到这几类之一，否则判未豁免。作者不得自批：审批事件 actor 会话 ≠ 豁免 transcript 作者会话。
LEDGER_EXEMPTION_APPROVAL_EVENT_TYPES = frozenset(
    {
        "OwnerConfirmationGranted",
        "EscalationAnswerApplied",
        "NatureJudgmentCaptured",
    }
)


def parse_ledger_exemption(line: str) -> dict[str, str] | None:
    """从一行源码里解析 `# ledger-scan-ok:` 豁免标记的三字段；无标记 / 格式不合返回 None。

    返回 {"reason", "transcript", "approved"}（均已 strip）。reason 空白（正则 `.+?` 匹配不到
    非空内容）→ 无匹配 → None（空理由不是合规豁免）。
    """
    m = LEDGER_SCAN_OK_RE.search(line)
    if m is None:
        return None
    reason = m.group("reason").strip()
    if not reason:
        return None
    return {
        "reason": reason,
        "transcript": m.group("anchor").strip(),
        "approved": m.group("approval").strip(),
    }


def reconcile_ledger_exemption(
    fields: dict[str, str],
    get_event: Callable[[str], EventRecord | None],
) -> tuple[bool, str]:
    """审批对账：判一个已解析的豁免标记是否**真的**过了审批链（fail-closed，无宽限态）。

    合规豁免（返回 (True, "")）须**全部**满足：
      1. reason / transcript / approved 三字段均非空；
      2. approved= 锚经 get_event 解析到一条真实 canonical 事件（存在性，索引读、非全量扫）；
      3. 该事件类型 ∈ LEDGER_EXEMPTION_APPROVAL_EVENT_TYPES（owner-confirm/escalation 通道产物）；
      4. 审批事件 actor 会话 ≠ 豁免 transcript 作者会话（作者不得自批，运动员不当裁判）。

    任一不满足 → (False, 原因)。这对应 con-guardtest-no-grace-state：approved 缺 / 锚不可解析 /
    事件类型不对 / actor=作者，四种情况一律判**未豁免命中**，不给坏审批放行窗口。
    """
    reason = fields.get("reason", "").strip()
    transcript = fields.get("transcript", "").strip()
    approved = fields.get("approved", "").strip()
    if not reason:
        return False, "reason 为空"
    if not transcript:
        return False, "transcript 锚为空"
    if not approved:
        return False, "approved 锚缺失（proposed 未批 = 未豁免）"

    ev = get_event(approved)
    if ev is None:
        return False, f"approved 锚 {approved} 不可解析到 canonical 事件（自造锚 = 绕过）"

    raw_type = getattr(ev, "event_type", None)
    event_type = getattr(raw_type, "value", raw_type)
    if event_type not in LEDGER_EXEMPTION_APPROVAL_EVENT_TYPES:
        return False, f"审批锚事件类型 {event_type!r} 不是合法审批载体"

    prov = getattr(ev, "provenance", None)
    approver_session = getattr(prov, "session_id", None) if prov is not None else None
    if approver_session is not None and approver_session == transcript:
        return False, "审批 actor 会话 = 豁免作者会话（作者自批，不合法）"

    return True, ""


@dataclass(frozen=True)
class AllRecordsCallSite:
    """源码里一处 `.all_records()` 调用点。line_text 为该行去首尾空白后的原文（含可能的行尾豁免注释）。"""

    rel_path: str
    lineno: int
    line_text: str


def find_all_records_call_sites(src_root: Path) -> list[AllRecordsCallSite]:
    """tokenize 扫 src_root 下所有 .py，找出 `.all_records()` 的**真实调用点**。

    用 tokenize（非纯 grep）以排除注释 / 字符串 / docstring 里的 "all_records" 字样，以及
    `def all_records` 定义点（无前导 `.`，天然不匹配）。判据 = 连续三 token `. all_records (`
    中 NAME token == 'all_records' 且前一个 OP == '.'、后一个 OP == '('。
    """
    sites: list[AllRecordsCallSite] = []
    for path in sorted(src_root.rglob("*.py")):
        try:
            with open(path, "rb") as fh:
                toks = list(tokenize.tokenize(fh.readline))
        except (tokenize.TokenError, SyntaxError, OSError):
            # 扫不动的文件（语法坏 / 编码坏）不静默吞成"0 命中" —— 但守护测试对 src 树的既有
            # 文件应能全部 tokenize；真出现扫不动的，交由调用方的完整性断言暴露，这里跳过不误报命中。
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, tok in enumerate(toks):
            if tok.type != tokenize.NAME or tok.string != LEDGER_GUARDED_SYMBOL:
                continue
            prev_tok = toks[i - 1] if i > 0 else None
            next_tok = toks[i + 1] if i + 1 < len(toks) else None
            if (
                prev_tok is not None
                and prev_tok.string == "."
                and next_tok is not None
                and next_tok.string == "("
            ):
                lineno = tok.start[0]
                line_text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
                sites.append(
                    AllRecordsCallSite(
                        rel_path=path.relative_to(src_root).as_posix(),
                        lineno=lineno,
                        line_text=line_text,
                    )
                )
    return sites


def call_site_is_exempted(
    site: AllRecordsCallSite,
    get_event: Callable[[str], EventRecord | None],
) -> bool:
    """一处调用点是否带**合规**豁免：行尾有 `# ledger-scan-ok:` 三字段标记 **且** 审批对账通过。

    标记缺 / 三字段不全 / 审批对账任一不过 → 未豁免（fail-closed）。get_event 只在标记在场时才
    被调用（用于审批锚对账），故对无标记的既有调用点零成本、不触碰账本。
    """
    fields = parse_ledger_exemption(site.line_text)
    if fields is None:
        return False
    ok, _reason = reconcile_ledger_exemption(fields, get_event)
    return ok


# ── 存量 grandfather 基线（批1 止血护栏 = 拦"新增"，不清"存量"）───────────────────────────
#
# v1 是 ledger-scale-sensitive-read-path@v1 的**阶段一：拦新增未豁免调用，不清存量**（CON-001,
# owner nj-c83883c9）。存量 30 处在册 roster = docs/LEDGER-READ-PATH-INVENTORY-2026-07-06.md
# （HOT-daemon 6 + HOT-cli 3 + WARM 18 + COLD 4 = 30 逻辑点；本基线按 tokenize 真实调用点计数，
# 批1 快照 = 32 处原始调用点，与 roster 逻辑点数量因行/点粒度差异略有出入）。
#
# 守护测试对这份**冻结基线**做棘轮（ratchet）断言：任一文件的未豁免调用点数 > 其基线值、或
# 基线外的文件出现任何未豁免调用点 → 判命中（= 新增，护栏该拦）。批1 当下 current == baseline
# → 绿；有人 bash 直写新增一处 → 该文件计数超基线 → 红。存量随批2–6 改造/过审豁免而**只减不增**，
# 由 B6-exempt-migrate（与本守护测试共写同一文件）逐步下调基线，终点批6 收敛到全 0。
#
# 【为何按"文件→计数"而非"文件:行号"】行号会随批2–6 编辑漂移；按 (文件→未豁免计数) 断言对行号
# 漂移免疫，且同文件多处相同调用行也能正确计数。已知弱点：同文件"删一处旧的+新增一处"计数不变会
# 漏（可接受的边界——主威胁"新增调用绕 hook"在计数上必现，且 B6 会随迁移收紧基线）。
LEDGER_ALL_RECORDS_GRANDFATHER_BASELINE: dict[str, int] = {
    # T-LRG-B2 P0 迁移后下调 5 个 P0 文件计数 (batch2 范围): cli/main 6→5 (仅 _escalation_goal_session_id
    # 迁移), orchestrator 3→2 (仅共享原语 _all_events_as_dicts 迁移; 5305/5341 属 P0 九点外, 见
    # debt debt-e4ab3fc859c7), m2x_daemons/reflow_reconcile/session_vitality 清零 → 移除 key
    # (Counter 对 0-命中文件不留 key)。其余 ~13 条 WARM/COLD 归 B6-exempt-migrate, 本任务不碰。
    "towow/cli/main.py": 5,
    "towow/l0/debt/registry.py": 1,
    "towow/l0/event_log/event_log.py": 1,
    "towow/l0/snapshot/consolidation_guard.py": 1,
    "towow/l0/snapshot/discard_tombstone.py": 1,
    "towow/l0/snapshot/gc.py": 2,
    "towow/l0/snapshot/reachability.py": 3,
    "towow/l0/snapshot/segment_align.py": 1,
    "towow/l0/snapshot/segment_compaction.py": 1,
    "towow/l1/interview/brief_publish.py": 2,
    "towow/l1/judgment_regression_harness.py": 1,
    "towow/l1/judgment_retrieval.py": 1,
    "towow/l2/orchestrator.py": 2,
    "towow/l2/stranded_batch_manifest.py": 1,
    "towow/l3/validation_runner.py": 1,
}


def scan_ledger_read_violations(
    src_root: Path,
    get_event: Callable[[str], EventRecord | None],
    baseline: dict[str, int] | None = None,
) -> list[AllRecordsCallSite]:
    """扫 src_root，返回**违反护栏的新增未豁免调用点**（超出 grandfather 基线的部分）。

    对每个文件：未豁免调用点数 ≤ 基线值的部分算存量（放行）；**超出基线的多余调用点**算新增违规。
    基线外文件（基线值=0）的任何未豁免调用点都算违规。返回的列表为空 = 护栏通过（无新增未豁免）。

    baseline 默认用 LEDGER_ALL_RECORDS_GRANDFATHER_BASELINE（冻结存量）；测试可传 {} 模拟
    "全新树"以证明护栏能抓 bash 直写绕过。
    """
    if baseline is None:
        baseline = LEDGER_ALL_RECORDS_GRANDFATHER_BASELINE
    unexempted: dict[str, list[AllRecordsCallSite]] = {}
    for site in find_all_records_call_sites(src_root):
        if call_site_is_exempted(site, get_event):
            continue
        unexempted.setdefault(site.rel_path, []).append(site)

    violations: list[AllRecordsCallSite] = []
    for rel_path, sites in unexempted.items():
        allowed = baseline.get(rel_path, 0)
        if len(sites) > allowed:
            # 稳定排序后取超出基线的尾部为"新增违规"（存量占满基线额度，多出来的是新增）。
            extra = sorted(sites, key=lambda s: s.lineno)[allowed:]
            violations.extend(extra)
    return violations


# ── 守护测试真跑对账卡（无 Python CI → 挂对账卡，不真跑冒红点）─────────────────────────────
#
# 本仓无 Python CI；层2 守护测试依赖 pytest 被**真跑**才有效。若没人跑、而被护住的源码又动过，
# 这张对账卡冒红点（stale=True），把"守护测试没真跑过 / 已过期"这条薄弱面显式暴露（brief 已
# declare 的处置 = 挂对账卡，而非假设 CI 存在）。红点逻辑复用 card_staleness 语义：被护文件在
# last_verified（上次真跑守护测试的日期）之后动过 → stale。
LEDGER_GUARDRAIL_CARD_ID = "ledger-read-static-guardrail"

# 被护栏守护、其改动应触发"重跑守护测试"的源码/spec 路径（repo 根相对）。
LEDGER_GUARDRAIL_GUARDED_PATHS = [
    "harness/src/towow/conformance.py",
    "harness/tests/conformance/test_ledger_read_guardrail.py",
    ".claude/hooks/PreToolUse-guard.sh",
]


def ledger_guardrail_conformance_card(repo: Path, last_verified: str) -> dict[str, Any]:
    """构造账本读护栏守护测试的对账卡 + 红点判定。

    last_verified = 上次守护测试真跑过的日期（YYYY-MM-DD；空串 = 从未真跑）。被护文件在其后动过、
    或 last_verified 为空 → stale=True（红点）。这就是"pytest 未真跑冒红点"的机器载体。
    """
    card = {
        "module_id": LEDGER_GUARDRAIL_CARD_ID,
        "code_paths": list(LEDGER_GUARDRAIL_GUARDED_PATHS),
        "spec_path": None,
        "verified_date": last_verified,
    }
    status = card_staleness(card, repo)
    # 从未真跑（last_verified 空）也算红点：守护测试没真跑过 = 护栏层2未生效。
    if not last_verified:
        status = {**status, "stale": True}
    return {**card, **status}
