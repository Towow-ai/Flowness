"""会话活性融合信号 + 通用心跳工具 — R11 自驱内核根机制 (owner-directed 2026-06-14).

# 为什么有这个模块 (R11 根 bug):
#   收割/重派层凭 daemon `state` 一个词判会话死活 (session_liveness.assess_session_liveness),
#   不看会话真实在不在干活、成果有没有落账。沉默≠死亡——慢会话/干完没自报/job 目录被归档的
#   会话被误判死亡 → 重派孪生 (烧额度+占并发位+连累下游)。这是共识熔断/卡死的共同根源。
#
# owner 的方向 (2026-06-14): 判死活最直接的方法是**看会话真实的过程**——读 transcript 看它在不在
#   thinking、token 变没变、报没报错、干完没。这个工具**给系统/Agent 用**(reaper + 任何要判兄弟
#   会话死活的 Agent),不是给人看的仪表(人本来就看得见)。两层:全局索引(现在有哪些活跃会话)+
#   查具体会话详情。这就是心跳 (heartbeat)。
#
# 信号源 (按耐久性/可信度; web+实测核实 2026-06-14):
#   1. canonical 账本 work-product (towow events) —— **永久耐久,从不自动删 = 骨干**。
#      DONE/PARTIAL 的首要判据 (TaskRunCompleted-success / PatchProposed / envelope)。
#   2. transcript 活动 (~/.claude/projects/<slug>/<sessionId>.jsonl) —— **默认 30 天保留**
#      (cleanupPeriodDays,启动时删超期),所以只是"近期实时在动"的 overlay,不能靠它"存在"判死活。
#   3. `claude agents --json` —— 官方全局索引;实测稳定给 id/state/name/sessionId/cwd/startedAt
#      (文档号称的 pid/status/waitingFor 实测大多没有,别信文档信实跑)。`state` 词单独不够
#      (死了 10 天的会话也显示 blocked),只作一个输入。
#   4. job state.json firstTerminalAt/tempo/updatedAt —— job 目录在时的辅助。
#
# 官方关键事实 (改了设计,见 docs 研究):
#   - process "stopped" 是正常的:supervisor 闲置 ~1h 停进程省资源,transcript+state 留盘,
#     attach/reply 从断点重启 → **STOPPED/MISSING ≠ 死,是"泊车可续"**;对未完成的活正确动作是
#     resume,不是起孪生。
#   - 任何 attach/logs 查询**可能唤醒**停止的进程 → liveness 查询优先 `claude agents --json`
#     (不唤醒),transcript 只读盘 (不用 attach 探活)。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog


# ── 裁决 ────────────────────────────────────────────────────────────────────────
class VitalityVerdict(StrEnum):
    """会话活性融合裁决 (给 reaper / Agent 做决策)。"""

    ALIVE_WORKING = "alive_working"      # transcript 在动 / 近期活动 → 绝不收割
    DONE = "done"                        # 干完了(canonical success / state=done / terminal) → 不重派
    PARKED_RESUMABLE = "parked_resumable"  # 有 canonical 半成品但没在动 → resume 续做,绝不起孪生
    STUCK_WAITING = "stuck_waiting"      # 卡在等输入(blocked 但还新) → escalate,不孪生
    DEAD = "dead"                        # 无产物 + 不在动 + 冷 → 真死,安全重派一个新的
    UNKNOWN = "unknown"                  # 信号不足 → 保守不收割,下轮再看


# reaper 对每个裁决该做什么 (供调用方参考; 真正动作在 orchestrator 收割逻辑):
#   ALIVE_WORKING / UNKNOWN → 不动 (别误杀)
#   DONE                     → 抑制重派 + 兜底补 GoalSessionTerminated
#   PARKED_RESUMABLE         → resume(attach/reply) 续做,绝不清戳起孪生
#   STUCK_WAITING            → escalate / dead-letter
#   DEAD                     → 清戳 + 重派一个新的 (真死才走这条; 这是唯一合法的重派)
_REAP_VERDICTS = frozenset({VitalityVerdict.DEAD})
_NEVER_TWIN_VERDICTS = frozenset(
    {VitalityVerdict.DONE, VitalityVerdict.PARKED_RESUMABLE},
)

# 默认阈值 (保守: 宁可多等一轮也别误杀; transcript 写入间隔实测秒~分级,真死才会冷到分钟/小时级)。
DEFAULT_ACTIVE_THRESHOLD_S = 180.0   # < 此值的最后活动 = 还在动
DEFAULT_COLD_THRESHOLD_S = 1800.0    # >= 此值不动 + 无产物 = 冷死


# daemon `state` 词里算"等输入"的 (来自 session_liveness 同一词表,避免漂移)。
_WAITING_STATES = frozenset({"blocked", "waiting"})
# 算"干完了"的 daemon state 词。
_DONE_STATES = frozenset({"done", "complete", "completed", "exited", "finished", "success", "succeeded"})


@dataclass(frozen=True)
class VitalitySignals:
    """喂给纯分类器的融合信号 (全部已采集好的事实)。"""

    # 耐久骨干 (canonical 账本)
    has_success_product: bool          # TaskRunCompleted-success / state=done / firstTerminalAt
    has_partial_product: bool          # canonical 半成品 (PatchProposed / envelope 等)
    # 实时 overlay (transcript)
    last_activity_age_s: float | None  # 距最后一条 transcript 活动多少秒 (None = 无 transcript)
    transcript_exists: bool
    # 辅助
    daemon_state: str | None           # claude agents --json / state.json 的 state 词
    is_waiting: bool                   # state ∈ {blocked, waiting}
    # 任务这次 run 是否已 aborted 终态 (TaskRunCompleted outcome 非 success: replan/不可恢复/advisor/外部)。
    # True = canonical "半成品" 其实是已终结的废 run, 不是可 resume 的活 (F-R11-LIVE-reaper-aborted-replan)。
    task_run_aborted: bool = False
    # ── 层① 焊接新增信号 (设计 10-layer1 §4.2/§4.3 / solution-3 修 A) ──
    # 持"活锁"(非 stale): 锁 registry session_signal 返回。None=查不到→保守当 False, 但绝不据此判死。
    # stale 锁(pid死/心跳超时)→ False = 废 run 不被锁误保护 (守 F-R11)。
    holds_live_lock: bool | None = False
    # 进程探活 os.kill(pid,0) 不唤醒。None=探针失败/单发CLI无持续进程→UNKNOWN, **绝不据此判 DEAD**。
    process_alive: bool | None = None
    # 复活预算耗尽 (T1.3 OTP max-restart-intensity) = 正向死亡证据之一 (非沉默)。
    revive_exhausted: bool = False
    # ── FB-5: 把 daemon-state-done 与 canonical 已验回收拆成独立两位 ──
    # has_success_product 出于 verdict (DONE) 的需要, 把 daemon state∈_DONE_STATES / firstTerminalAt
    # 也算 success 证据 —— 但那是 daemon/job 状态词推断, 不是 canonical 真验到了产物。本位**只**反映
    # canonical 自己的判断 (work-product 扫描, 在 daemon-state 覆盖之前采集):
    #   True  = canonical 真解析到 success 产物 (TaskRunCompleted-success / envelope 等)。
    #   False = canonical 有可解析记录但非 success (半成品 / 已 aborted 终态)。
    #   None  = canonical 查不到 (task_id=None 解析不出键 / 无对应事件) → unknown, 绝不用 daemon-state 冒充。
    # 拆开后 CLI / 任何消费方能看出 "success" 到底是 canonical 实证还是 daemon 状态词驱动 (杜绝循环论证)。
    canonical_verified_recovery: bool | None = None
    # T-LRF-03: transcript 尾部【最后一条】is_error block 的真实错误文本 (会话终态失败成因)。
    # 喂 @dispatch-failure-signal-contract@v1 字段2 signal_text 的合法来源之一 (死亡证据/真错因);
    # None = 尾部无 is_error 文本。绝不用 _retry_marker_last_error 的上次派发载荷冒充它。
    transcript_tail_error_text: str | None = None

    def is_active(self, active_threshold_s: float) -> bool:
        return self.last_activity_age_s is not None and self.last_activity_age_s < active_threshold_s

    def is_cold(self, cold_threshold_s: float) -> bool:
        return self.last_activity_age_s is None or self.last_activity_age_s >= cold_threshold_s


def derive_exec_made_progress(
    signals: VitalitySignals,
    *,
    active_threshold_s: float = DEFAULT_ACTIVE_THRESHOLD_S,
) -> bool:
    """@dispatch-failure-signal-contract@v1 字段1 made_progress 的【派生契约】(T-LRF-03)。

    会话死前是否做过有意义工作。合法信号源只有二:
      (a) canonical 半成品存在 (has_partial_product=True, 如 PatchProposed / envelope);
      (b) transcript 真实【活动/新鲜度】—— 距最近一条 transcript 活动在新鲜阈值内
          (is_active = last_activity_age_s 不为 None 且 < active_threshold_s)。

    【禁止来源】transcript 文件【存在】本身: transcript 启动初始化即建, 文件存在 ≠ 做过工作;
    故 transcript_exists 与裸 "last_activity_age_s is not None" 都【非】合法来源 (与共识
    subprocess-heartbeat-stuck-detection@v1 钉死的"裸 mtime/state.json 会撒谎"同源同病)。
    判别尺: 启动后【零活动】即死的会话 made_progress 必须 == False (才能落入 deterministic/unknown)。

    这是 orchestrator 收割路径 exec_made_progress 接线的【单一真相源】(orchestrator 与单测同调它,
    不各写一份 reimplementation)。
    """
    return signals.has_partial_product or signals.is_active(active_threshold_s)


@dataclass(frozen=True)
class SessionVitality:
    """一个会话的活性快照 — reaper / status / Agent 共用。"""

    session_id: str                    # short_id 或 full UUID (调用方传啥用啥)
    verdict: VitalityVerdict
    signals: VitalitySignals
    reason: str                        # 人/Agent 可读的一句话理由
    # 详情 (给 Agent/调试看"它在干啥")
    last_transcript_type: str | None = None
    token_total: int | None = None     # transcript 里累计 output tokens (动没动)
    has_recent_error: bool = False
    name: str | None = None
    transcript_path: str | None = None

    @property
    def should_redispatch(self) -> bool:
        """True = 真死,清戳重派一个新的是合法的。

        F-R11-REVIEW (over-reap 根修): aborted run 虽判 DEAD-for-cleanup, 但**绝不**靠孪生重派——
        它的替身已由正常 replan (task 回 in_progress) + B2(已清执行戳) + ready 重扫 owns。把"不孪生
        aborted run"焊进属性本身 (true by construction): 任何调用方拿 should_redispatch 起孪生都不会
        误孪一个本 run 已显式终结、替身已另有归属的 task。"""
        return self.verdict in _REAP_VERDICTS and not self.signals.task_run_aborted

    @property
    def must_not_twin(self) -> bool:
        """True = 绝不能起孪生 (干完了 / 有半成品该 resume)。"""
        return self.verdict in _NEVER_TWIN_VERDICTS


# ── 纯分类器 (决策表; 可单测,不碰 I/O) ────────────────────────────────────────────
def classify_vitality(
    signals: VitalitySignals,
    *,
    active_threshold_s: float = DEFAULT_ACTIVE_THRESHOLD_S,
    cold_threshold_s: float = DEFAULT_COLD_THRESHOLD_S,
) -> VitalityVerdict:
    """融合信号 → 裁决 (设计 10-layer1 §4.2 决策表; DEAD 只由正向死亡证据, FLP 偏判活)。

    按序匹配,第一个命中即返回 (序与 awareness.liveness.classify_liveness 严格对齐 = §T1.2 权威表;
    T-RMD-S3-FLP 合一: 同一会话状态喂两核必得同裁决, tests/integration/test_s3_flp.py 钉死):
      1. 有 success 产物 (canonical/terminal)          → DONE
      2. transcript 在动 (最后活动 < active 阈值)        → ALIVE_WORKING (绝不收割)
      3. 持活锁(holds_live_lock 非 stale)且进程未确死    → PARKED_RESUMABLE (强活, 绝不死)
      4. 任务这次 run 已 aborted 终态                   → DEAD (废 run 正向证据)
      5. 有 canonical 半成品                            → PARKED_RESUMABLE (resume,不孪生)
      6. 进程确凿没了 (process_alive is False)          → DEAD (pid 没了=正向死亡; zombie lock 回收)
      7. 复活预算耗尽 (revive_exhausted)               → DEAD (OTP 正向终态)
      8. 等输入(blocked) 且还没冷                       → STUCK_WAITING (escalate)
      9. 否则 (含冷+无产物+探针不确)                    → UNKNOWN (保守不收割, 下轮再看)

    🔴 两条偏判活硬约束 (红队测覆盖): (1) process_alive is None (探针失败/单发CLI) **绝不** DEAD;
    (2) holds_live_lock is True 且进程未确凿死 (process_alive is not False) **绝不** DEAD。DEAD 的三条
    正向来路: aborted / pid 没了 / 复活预算耗尽 —— 全是显式证据, 不是沉默。旧"冷+无产物→DEAD"(FLP 陷阱:
    沉默既可能死也可能慢/泊车) 已拆除, 这是 owner 实战#2 (在思考被删 / 限流被杀) 的根治。

    持活锁与 F-R11 的调和 (规则 3 排 4/6 前): 活锁=非 stale; 废 run/死会话的锁会因 pid 死或心跳超时变
    stale → holds_live_lock=False → 不命中规则 3 → 落到规则 4 (aborted→DEAD) / 规则 6 (pid没了→DEAD)
    被正确回收。"持锁恒不死"只保护真在事务中的活会话, 不让废 run 永占执行位 (T-LRF-04 不回退)。
    规则 3 的 `process_alive is not False` 守卫 (T-RMD-S3-FLP 合一加): 本核 holds_live_lock 上游
    (_signals_for_vitality) 已把 stale 锁过滤成 False, 故 holds_live_lock=True 时 process_alive 只会是
    None(租约内单发CLI)/True(pid活), **绝不**是 False → 守卫是生产 no-op; 它只让决策表本身不依赖上游过滤的
    隐含假设(自鲁棒), 并与 liveness 核 (其 holds_live_lock 是原始信号, 必须自守 zombie lock) 严格同序。
    """
    if signals.has_success_product:
        return VitalityVerdict.DONE
    if signals.is_active(active_threshold_s):
        return VitalityVerdict.ALIVE_WORKING
    # 新3 (设计 §4.2): 持活锁恒不死 = 强活 (Kleppmann 不杀持锁者)。活锁=非 stale; 废 run/死会话的
    # 锁会变 stale → holds_live_lock=False → 落到下面 DEAD 规则 (守 F-R11 不回退)。
    # `process_alive is not False` 守卫 (T-RMD-S3-FLP 合一): 与 liveness 核同序——持锁但 pid 确凿没了 =
    # zombie lock, 不命中此条, 落规则6 DEAD 回收。本核上游已过滤 stale 锁故守卫是生产 no-op (持锁时
    # process_alive 只会 None/True), 仅令决策表自鲁棒 + 跨核等价。探针失败 None is not False→True 仍 PARKED。
    if signals.holds_live_lock and signals.process_alive is not False:
        return VitalityVerdict.PARKED_RESUMABLE
    if signals.task_run_aborted:
        return VitalityVerdict.DEAD
    if signals.has_partial_product:
        return VitalityVerdict.PARKED_RESUMABLE
    # 新6: pid 确凿没了 = 正向死亡证据。🔴 process_alive is None (探针失败/单发CLI) **绝不**走这条。
    if signals.process_alive is False:
        return VitalityVerdict.DEAD
    # 新7: 复活预算耗尽 (T1.3 OTP) = 正向终态, 非沉默。
    if signals.revive_exhausted:
        return VitalityVerdict.DEAD
    if signals.is_waiting and not signals.is_cold(cold_threshold_s):
        return VitalityVerdict.STUCK_WAITING
    # 🔴 拆掉旧"冷+无产物→DEAD"的 FLP 陷阱 (沉默既可能死也可能慢/泊车)。冷而无正向死亡证据 → UNKNOWN
    # 保守不收割 (owner 实战#2 根治: 在思考被删 / 限流被杀 不再发生)。真死靠 process_alive==False 接。
    return VitalityVerdict.UNKNOWN


_REASONS = {
    VitalityVerdict.DONE: "已完成(canonical success/terminal) — 不重派,缺自报兜底补",
    VitalityVerdict.ALIVE_WORKING: "transcript 近期在动 — 绝不收割",
    VitalityVerdict.PARKED_RESUMABLE: "有 canonical 半成品但没在动 — resume 续做,绝不起孪生",
    VitalityVerdict.STUCK_WAITING: "卡在等输入且还新 — escalate,不孪生",
    VitalityVerdict.DEAD: "正向死亡证据 (进程没了 os.kill / 废 run aborted / 复活预算耗尽) — 安全重派",
    VitalityVerdict.UNKNOWN: "信号不足 — 保守不收割,下轮再看",
}

# DEAD 的两条来路理由不同: task_run_aborted 走这条 (有产物但是废 run), 冷死走 _REASONS[DEAD]。
_REASON_ABORTED_RUN = "任务这次 run 已中止(replan/不可恢复/advisor/外部) — 废 run,收割重走,不当 parked 占位"


# ── transcript 读取 (实时 overlay; 只读盘,不 attach,不唤醒进程) ────────────────────
def _default_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def find_transcript_path(
    session_id: str,
    *,
    link_scan_path: str | None = None,
    projects_dir: Path | None = None,
) -> Path | None:
    """定位会话 transcript。优先 state.json 的 linkScanPath; 否则按 sessionId(UUID) 跨 project glob。

    sessionId 是全局唯一 UUID,glob `projects/*/<sessionId>.jsonl` 比 slug 编码可靠 (slug 把
    非 ascii 路径压成 dash,易漂)。short_id 形态也兜底匹配 `<short>*.jsonl`。
    """
    if link_scan_path:
        p = Path(link_scan_path)
        if p.exists():
            return p
    base = projects_dir if projects_dir is not None else _default_projects_dir()
    if not base.is_dir():
        return None
    # 精确 UUID 命名
    hits = list(base.glob(f"*/{session_id}.jsonl"))
    if hits:
        return hits[0]
    # short_id 前缀兜底 (transcript 文件名以 short_id 开头)
    hits = list(base.glob(f"*/{session_id}*.jsonl"))
    return hits[0] if hits else None


# Bash tool_result 通用前导状态行 "Exit code N" (实测真实形状: content="Exit code 1\n<stderr/stdout>",
# stderr 含 Traceback / "error: ..." 等真成因, 经探针验证 stderr 确被收进 is_error content)。
_BASH_EXIT_CODE_PREFIX = re.compile(r"^Exit code \d+\s*\n?", re.IGNORECASE)


def _extract_error_block_text(block: dict[str, object]) -> str | None:
    """从一个 is_error tool_result block 抽出【真实失败成因文本】(喂 dispatch 失败分类的结构性签名)。

    生产真实形状 (实测 ~/.claude/projects): tool_result block 的 content 是 str, 以通用前导行
    "Exit code N\\n" 起头, 后接命令真实 stderr/stdout (如 "error: capsule injection failed ..." /
    Python Traceback)。list-of-text-block 形态 ([{type:text,text}]) 作兜底也抽。拿不到文本 → None。

    ★【剥通用退出包装行】(T-LRF-03 经验验证, 非 tuning): "Exit code N" 是 Bash 退出状态包装、不是失败
    【成因】。留着它会让 K6 true_death 正则 (含 "exit code") 在【结构性签名之前】把【每一条】Bash 失败
    都遮成 transient (因每条失败都有 "Exit code N"), 致真结构失败 (capsule 装配拒绝 / 启动期自检崩 /
    参数解析崩) 的 deterministic 在 retry_count==0 永不可达 —— 这正是 @dispatch-failure-signal-contract@v1
    字段2 要的"载真实成因"。剥掉它只删【伪】的 exit-code 匹配; 所有实质 K6 模式 (network/rate-limit/
    model/killed/segfault) 仍作用于真输出、仍先于结构签名命中, 故剥包装【不会】把真瞬态翻成 deterministic
    (守 conservative-deterministic)。剥后为空 → 保留原文 (不丢光信号)。
    """
    content = block.get("content")
    text: str | None
    if isinstance(content, str):
        text = content or None
    elif isinstance(content, list):
        parts = [
            sub.get("text")
            for sub in content
            if isinstance(sub, dict) and isinstance(sub.get("text"), str)
        ]
        text = "\n".join(p for p in parts if p) or None
    else:
        text = None
    if text is None:
        return None
    stripped = _BASH_EXIT_CODE_PREFIX.sub("", text, count=1).strip()
    return stripped or text


def _parse_transcript_tail(
    transcript_path: Path | None,
    *,
    now_fn: Callable[[], float] | None = None,
    tail_bytes: int = 131072,
) -> tuple[float | None, str | None, int | None, bool, str | None]:
    """读 transcript 尾部, 返回 (last_activity_age_s, last_type, token_total, has_error, error_text)。

    内部核 (单一真相源): read_transcript_activity 公开 4 元组包装它 (向后兼容既有契约),
    assess_vitality 直取 5 元组含 error_text。error_text = 尾部【最后一条】is_error block 的真实
    错误文本 (会话终态失败成因), 喂 @dispatch-failure-signal-contract@v1 的 signal_text (T-LRF-03);
    禁止用 _retry_marker_last_error 的上次派发载荷冒充失败成因。

    只读尾部 tail_bytes 避免巨型文件爆内存。耐久性注意: transcript 30 天会被清, "读不到"不代表
    "没干过"——耐久判据看 canonical 账本, 本函数只给 "近期在不在动 + 尾部真错因" 的 overlay。
    """
    now = (now_fn or time.time)()
    if transcript_path is None or not transcript_path.exists():
        return (None, None, None, False, None)
    try:
        size = transcript_path.stat().st_size
        with transcript_path.open("rb") as f:
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return (None, None, None, False, None)

    last_ts_epoch: float | None = None
    last_type: str | None = None
    token_total: int | None = None
    has_error = False
    error_text: str | None = None
    for line in tail.splitlines():
        try:
            e = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        ts = e.get("timestamp")
        if ts:
            ep = _parse_iso_epoch(ts)
            if ep is not None and (last_ts_epoch is None or ep >= last_ts_epoch):
                last_ts_epoch = ep
                last_type = e.get("type")
        msg = e.get("message")
        if isinstance(msg, dict):
            usage = msg.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
                token_total = (token_total or 0) + usage["output_tokens"]
            content = msg.get("content")
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("is_error"):
                        has_error = True
                        # 尾部顺序 = 时序; 保留【最后一条】错因 (最近的终态失败成因)。
                        blk_text = _extract_error_block_text(blk)
                        if blk_text is not None:
                            error_text = blk_text
    # 文件 mtime 作为活动时间的兜底 (尾部没带时间戳的 entry 时)
    age: float | None
    if last_ts_epoch is not None:
        age = max(0.0, now - last_ts_epoch)
    else:
        try:
            age = max(0.0, now - transcript_path.stat().st_mtime)
        except OSError:
            age = None
    return (age, last_type, token_total, has_error, error_text)


def read_transcript_activity(
    transcript_path: Path | None,
    *,
    now_fn: Callable[[], float] | None = None,
    tail_bytes: int = 131072,
) -> tuple[float | None, str | None, int | None, bool]:
    """读 transcript 尾部,返回 (last_activity_age_s, last_type, token_total, has_recent_error)。

    公开 4 元组契约不变 (既有消费方); 错因文本经内部 _parse_transcript_tail 5 元组给 assess_vitality。
    """
    age, last_type, token_total, has_error, _ = _parse_transcript_tail(
        transcript_path, now_fn=now_fn, tail_bytes=tail_bytes,
    )
    return (age, last_type, token_total, has_error)


def _parse_iso_epoch(ts: str) -> float | None:
    """ISO8601 (含 Z) → epoch 秒。失败返 None。"""
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _rec_epoch(rec: object) -> float | None:
    """事件记录 rec.timestamp → UTC epoch 秒 (run-scoped abort 时间比较用)。

    rec.timestamp 是 EventRecord.timestamp (pydantic datetime; 从盘上 ISO-with-Z 解析 = tz-aware
    UTC)。防御性: naive datetime 一律当 UTC (躲 .timestamp() 把 naive 按本地时区 +8 的陷阱, 见
    reference_v3_eventlog_utc_vs_filesystem_local); 字符串走 _parse_iso_epoch。拿不到/解析失败 → None
    (调用方据此**不**计入 abort, 偏保守 = under-reap 安全方向)。"""
    from datetime import UTC, datetime
    ts = getattr(rec, "timestamp", None)
    if ts is None:
        return None
    if isinstance(ts, datetime):
        try:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return float(ts.timestamp())
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(ts, str):
        return _parse_iso_epoch(ts)
    return None


# ── 全局索引 (包官方 `claude agents --json`,不自己扫文件) ──────────────────────────
def list_sessions(
    *,
    include_done: bool = True,
    cwd_filter: str | None = None,
    runner: Callable[[list[str]], str] | None = None,
) -> list[dict]:
    """官方全局索引: 包 `claude agents --json [--all]`。

    实测稳定字段: id / state / name / sessionId / cwd / startedAt / kind。
    runner 可注入 (测试用); 默认真跑 claude CLI (15s 超时,失败返 [])。
    """
    cmd = ["claude", "agents", "--json"]
    if include_done:
        cmd.append("--all")
    try:
        out = (runner or _run_claude)(cmd)
        data = json.loads(out)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    rows = [d for d in data if isinstance(d, dict)]
    if cwd_filter:
        rows = [d for d in rows if d.get("cwd") == cwd_filter]
    return rows


def roster_session_ids(
    *,
    runner: Callable[[list[str]], str] | None = None,
) -> frozenset[str] | None:
    """官方全局索引的 id 集合 (`claude agents --json --all`), 供 reconcile 判"这个 gsid 官方还认不认"。

    与 `list_sessions` 的关键区别 (FLP-safety): 查询失败 (subprocess/解析异常) 返回 **None**,
    成功但真的没有条目返回 **空 frozenset**——调用方必须能区分"查不到=不可信、别下判断"与
    "查到了、官方确实不认得这个 id=可信的死亡证据", 否则一次瞬态子进程失败会让所有 active_relay
    会话被误判"官方不认识" (roster=[] 时 gsid 必不在其中) 从而集体误杀。`list_sessions` 两处既有
    CLI 报表消费方只做展示、无此风险, 故不改它的既有契约 (返回 [] 兼容), 另开一个函数。

    stale-relay-reap 根治用: reconcile 每轮只调一次 (不逐 session 各起一次子进程), 结果集在本轮内
    对所有候选 gsid 复用。
    """
    try:
        out = (runner or _run_claude)(["claude", "agents", "--json", "--all"])
        data = json.loads(out)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return frozenset(
        str(d["id"]) for d in data if isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"]
    )


def _run_claude(cmd: list[str]) -> str:
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, timeout=15, check=False,
    ).stdout


# ── 融合裁决 (一个会话) ───────────────────────────────────────────────────────────
def assess_vitality(
    session_id: str,
    *,
    task_id: str | None = None,
    agents_entry: dict | None = None,
    link_scan_path: str | None = None,
    first_terminal_at: str | None = None,
    work_product_fn: Callable[[str], tuple[bool, bool]] | None = None,
    event_log: EventLog | None = None,
    now_fn: Callable[[], float] | None = None,
    active_threshold_s: float = DEFAULT_ACTIVE_THRESHOLD_S,
    cold_threshold_s: float = DEFAULT_COLD_THRESHOLD_S,
    projects_dir: Path | None = None,
    task_run_aborted: bool = False,
    aborted_after_ts: float | None = None,
    towow_dir: Path | None = None,
    process_alive_fn: Callable[[str], bool | None] | None = None,
    holds_lock_fn: Callable[[str], bool | None] | None = None,
    revive_exhausted: bool = False,
) -> SessionVitality:
    """融合所有信号给一个会话的活性裁决。

    task_id: 🔴 C1 修复 (review 抓出) —— canonical 产物事件按 task_id 键 (不是 bg/goal session id)。
      reaper 手里有 marker_task_id, **必须传**, 否则 DONE-via-canonical 永远查不到 (孪生复发)。
    work_product_fn(key) -> (has_success, has_partial): 查 canonical 账本耐久产物 (测试注入用)。
      不给时若给了 event_log 用默认扫描 (按 task_id 主键 + session_id 兜底); 都不给则 (False, False)。
    agents_entry: `claude agents --json` 里这个会话那条 (给 state/name/sessionId)。
    aborted_after_ts: F-R11-REVIEW (over-reap 根修) —— 本 marker 的 recorded_at (epoch)。event_log
      扫描路径只把时间戳 ≥ 它的 abort 计入 task_run_aborted (run-scoped, 防活会话 run2 继承 run1 的
      abort 被误杀)。None = 不过滤 (诊断路径)。work_product_fn 注入路径不受影响 (该路径靠显式
      task_run_aborted 参数)。
    """
    state_word = (agents_entry or {}).get("state")
    state_lc = state_word.lower() if isinstance(state_word, str) else None
    name = (agents_entry or {}).get("name")
    # transcript sessionId: 优先 agents_entry 的 full UUID
    tx_id = (agents_entry or {}).get("sessionId") or session_id

    aborted = task_run_aborted  # 显式传 (work_product_fn 路径靠它); reaper 走 event_log 自动检测
    if work_product_fn is not None:
        has_success, has_partial = work_product_fn(task_id if task_id is not None else tx_id)
    elif event_log is not None:
        has_success, has_partial, scanned_aborted = scan_canonical_work_product(
            event_log, task_id=task_id, session_id=tx_id,
            aborted_after_ts=aborted_after_ts,
        )
        aborted = aborted or scanned_aborted
    else:
        has_success, has_partial = (False, False)

    # FB-5: 在 daemon-state / job-terminal 覆盖之前, 把 canonical 自己的判断单独定格。
    # 这两位**只**来自 work_product_fn / canonical 扫描 (与 first_terminal_at 这种 job-state 信号、
    # state∈_DONE_STATES 这种 daemon 状态词彻底无关)。canonical_verified_recovery 据此拆出独立一位:
    #   有 success → True; 无 success 但有可解析记录(半成品/aborted) → False; 都没有 → None(unknown)。
    canonical_success = has_success
    canonical_partial = has_partial
    canonical_verified_recovery: bool | None
    if canonical_success:
        canonical_verified_recovery = True
    elif canonical_partial or aborted:
        canonical_verified_recovery = False
    else:
        canonical_verified_recovery = None

    # daemon-state / terminal 也算 success 证据 (喂 verdict 的 DONE 判据, 行为不变)
    if first_terminal_at or (state_lc in _DONE_STATES):
        has_success = True

    tx_path = find_transcript_path(
        tx_id, link_scan_path=link_scan_path, projects_dir=projects_dir,
    )
    age, last_type, token_total, has_error, tail_error_text = _parse_transcript_tail(
        tx_path, now_fn=now_fn,
    )

    # 层① 焊接: 采集进程探活 + 活锁信号 (设计 §4.2/§4.3)。注入 fn 优先(测试); 否则给了 towow_dir
    # 用锁 registry session_signal (进程 os.kill + 活锁=非 stale, 守 F-R11); 都没有 → 保守 None/False
    # (退化成只靠 canonical/transcript, 不崩、绝不据此判死)。
    proc_alive: bool | None = None
    holds_lock: bool | None = False
    if process_alive_fn is not None:
        proc_alive = process_alive_fn(session_id)
    if holds_lock_fn is not None:
        holds_lock = holds_lock_fn(session_id)
    if process_alive_fn is None and holds_lock_fn is None and towow_dir is not None:
        from towow.l1.session_lock import SessionLockRegistry
        proc_alive, holds_lock = SessionLockRegistry(towow_dir, "execution").session_signal(
            session_id, now=now_fn() if now_fn is not None else None,
        )

    signals = VitalitySignals(
        has_success_product=has_success,
        has_partial_product=has_partial,
        last_activity_age_s=age,
        transcript_exists=tx_path is not None,
        daemon_state=state_lc,
        is_waiting=(state_lc in _WAITING_STATES),
        task_run_aborted=aborted,
        holds_live_lock=holds_lock,
        process_alive=proc_alive,
        revive_exhausted=revive_exhausted,
        canonical_verified_recovery=canonical_verified_recovery,
        transcript_tail_error_text=tail_error_text,
    )
    verdict = classify_vitality(
        signals, active_threshold_s=active_threshold_s, cold_threshold_s=cold_threshold_s,
    )
    reason = _REASONS[verdict]
    if verdict is VitalityVerdict.DEAD and aborted:
        reason = _REASON_ABORTED_RUN  # 废 run 收割,与"冷死"区分
    return SessionVitality(
        session_id=session_id,
        verdict=verdict,
        signals=signals,
        reason=reason,
        last_transcript_type=last_type,
        token_total=token_total,
        has_recent_error=has_error,
        name=name if isinstance(name, str) else None,
        transcript_path=str(tx_path) if tx_path else None,
    )


def scan_canonical_work_product(
    event_log: EventLog,
    *,
    task_id: str | None = None,
    session_id: str | None = None,
    aborted_after_ts: float | None = None,
) -> tuple[bool, bool, bool]:
    """扫 canonical 账本,查耐久 work-product。按 task_id (主键) 或 session_id (兜底) 匹配。

    🔴 C1 修复 (2026-06-14, review 抓出的 critical): TaskRunCompleted / 产物事件实证 100% 带
    `after_state.task_id`、0% 带 goal_session_id, 且 provenance.session_id 是 worker 的
    `sess-work-…` 执行会话 id。而 reaper 手里的 gsid 是 daemon **bg 短 id** —— 与 worker session id /
    task_id 是两套命名空间。旧版只按 session_id(=bg id) 匹配 → 真实 reaper 路径**永远查不到产物** →
    DONE 判据退化 → 干完没自报的会话照旧被判 DEAD 重派孪生 (= 本应根治的根 bug 原样复发)。
    所以 **reaper 必须传 task_id**; session_id 仅作兜底 (能直接拿到 worker session id 的场景)。

    Returns (has_success, has_partial, has_aborted_run):
      has_success    = 有 TaskRunCompleted 且 outcome=success。
      has_partial    = 有 PatchProposed / envelope / FixCompleted 等耐久产物 (或非 success 完成)。
      has_aborted_run= 有 TaskRunCompleted 且 outcome≠success (replan/不可恢复/advisor/外部 终态) ——
                       F-R11-LIVE-reaper-aborted-replan: 区分"已终结的废 run"(收割) 与"crash 在中途
                       的真半成品"(resume)。前者有 TaskRunCompleted-abort, 后者只有 PatchProposed 无完成。
    canonical 永久耐久(从不自动删),是判 DONE/PARTIAL 的骨干 (transcript 只 30 天)。

    aborted_after_ts (F-R11-REVIEW over-reap 根修): has_success/has_partial 故意 **task-scoped**
    (DONE 抑制 / PARKED 都"work 已出货就别动", 与哪个 run 无关, 安全)。但 has_aborted_run 会触发
    **收割**裁决 (DEAD) —— 若也 task-scoped, 一个 task 的 run1 aborted 后, 重派的活会话 run2(transcript
    暂静默)会**继承** run1 的 abort → 被误判 DEAD → 误杀活会话 (两套独立 review 坐实的 critical)。
    所以只有 abort 必须 **run-scoped**: 传本 marker 的 recorded_at, 只有时间戳 ≥ 它 (= 本 run 出生
    之后) 的 abort 才算"本 run 废了"; run1 的 abort 早于 run2.recorded_at → 不计入 run2。None = 不做
    时间过滤 (退回 task-scoped) —— 仅供无 marker 的诊断路径 (CLI vitality / build_work_product_map),
    它们不收割, over-report aborted 无害。reaper 必须传具体 floor。
    """
    from towow.l2.orchestrator import _extract_goal_session_id, _unwrap_stub_rewrap

    success = False
    partial = False
    aborted = False
    _PARTIAL_TYPES = {
        "PatchProposed", "TransactionEnvelopeSubmitted", "FixProposed",
        "FixCompleted", "CommitAccepted",
    }
    # T-LRG-B2 P0 迁移: warm committed_index snapshot 替代全量磁盘 re-scan (等价已提交事件集, O(索引))
    for rec in event_log.committed_index().records():
        etype, payload = _unwrap_stub_rewrap(rec)
        after = payload.get("after_state", payload) if isinstance(payload, dict) else {}
        matched = False
        if task_id is not None:
            ev_task = (after.get("task_id") if isinstance(after, dict) else None) or (
                payload.get("task_id") if isinstance(payload, dict) else None
            )
            if ev_task == task_id:
                matched = True
        if not matched and session_id is not None:
            if _extract_goal_session_id(payload) == session_id:
                matched = True
            else:
                prov = getattr(rec, "provenance", None)
                sid = getattr(prov, "session_id", None) if prov is not None else None
                matched = sid == session_id
        if not matched:
            continue
        if etype == "TaskRunCompleted":
            outcome = after.get("outcome") if isinstance(after, dict) else None
            if outcome == "success":
                success = True
            else:
                partial = True   # 非 success 完成也算干过活 (task-scoped, 不触发收割, 安全)
                # F-R11-REVIEW: abort **必须 run-scoped** —— 只有本 run 出生(recorded_at)之后的 abort
                # 才算本 run 废了。否则 run2(活会话)继承 run1 的 abort → 误判 DEAD → 误杀。None floor =
                # 不过滤 (诊断路径退回 task-scoped)。`>=` 不 `>`: run1 自身的 abort 必 ≥ run1.recorded_at,
                # 同秒退化也要让它算给 run1; run2.recorded_at 严格晚于 run1 的 abort 故 run2 永不误收。
                if aborted_after_ts is None:
                    aborted = True
                else:
                    _ev_epoch = _rec_epoch(rec)
                    if _ev_epoch is not None and _ev_epoch >= aborted_after_ts:
                        aborted = True
        elif etype in _PARTIAL_TYPES:
            partial = True
    return (success, partial, aborted)


def build_work_product_map(event_log: EventLog) -> dict[str, tuple[bool, bool, bool]]:
    """一遍扫账本,建 {task_id: (has_success, has_partial, has_aborted_run)} —— 全局索引(避免 N 次全扫)。

    🔴 C1 修复: 按 **task_id** 键 (产物事件的稳定键, 实证 100% 带 after_state.task_id), 不是
    session_id (产物事件不带 bg/goal session id)。CLI 用它时按会话的 task_id (从 pending marker) 查。
    has_aborted_run (F-R11-LIVE-reaper-aborted-replan): 有 TaskRunCompleted outcome≠success = 已终结的
    废 run, 给 classify 收割而非当 parked 保留 (与 scan_canonical_work_product 同义)。
    """
    from towow.l2.orchestrator import _unwrap_stub_rewrap
    from towow.schemas.enums import EventType

    _PARTIAL_TYPES = {
        "PatchProposed", "TransactionEnvelopeSubmitted", "FixProposed",
        "FixCompleted", "CommitAccepted",
    }
    succ: set[str] = set()
    part: set[str] = set()
    abrt: set[str] = set()
    # f-perf2-vitality-full-materialize-for-narrow-type-scan: this function only ever cares about 6
    # event types, but committed_index().records() paid a full pydantic parse of the WHOLE committed
    # stream (629k+ records on the real ledger, 91% of them NodeTouched stub-rewraps of unrelated
    # types) just to discard almost everything a moment later — measured ~28s / +4.4GB RSS, the
    # dominant cost of `towow vitality` (5.4GB peak). get_events_by_types_or_stub_kinds narrows the
    # scan to records whose (unwrapped) logical type is actually one of these 6, at the index layer
    # (byte-level pre-filter on LazyEventIndex — see its docstring), so the O(全账本) cost collapses
    # to O(matching records). Same unwrap (_unwrap_stub_rewrap) applied below, so the (etype, payload)
    # seen per record is identical to the old full-scan loop — only which records reach the loop body
    # changed, not what happens to each one.
    _WANTED_TYPES = frozenset({
        EventType.TASK_RUN_COMPLETED, EventType.PATCH_PROPOSED,
        EventType.TRANSACTION_ENVELOPE_SUBMITTED, EventType.FIX_PROPOSED,
        EventType.FIX_COMPLETED, EventType.COMMIT_ACCEPTED,
    })
    for rec in event_log.get_events_by_types_or_stub_kinds(_WANTED_TYPES):
        etype, payload = _unwrap_stub_rewrap(rec)
        after = payload.get("after_state", payload) if isinstance(payload, dict) else {}
        tid = (after.get("task_id") if isinstance(after, dict) else None) or (
            payload.get("task_id") if isinstance(payload, dict) else None
        )
        if not tid:
            continue
        if etype == "TaskRunCompleted":
            if isinstance(after, dict) and after.get("outcome") == "success":
                succ.add(tid)
            else:
                part.add(tid)
                abrt.add(tid)
        elif etype in _PARTIAL_TYPES:
            part.add(tid)
    keys = succ | part
    return {k: (k in succ, k in part, k in abrt) for k in keys}


__all__ = [
    "DEFAULT_ACTIVE_THRESHOLD_S",
    "DEFAULT_COLD_THRESHOLD_S",
    "SessionVitality",
    "VitalitySignals",
    "VitalityVerdict",
    "assess_vitality",
    "build_work_product_map",
    "classify_vitality",
    "derive_exec_made_progress",
    "find_transcript_path",
    "list_sessions",
    "read_transcript_activity",
    "roster_session_ids",
    "scan_canonical_work_product",
]
