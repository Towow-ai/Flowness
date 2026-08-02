"""哨兵⑥ · 检测规则 A1-A8 + done-real (纯函数, 像 stress_invariants)。

设计真相源: 40-layer6-sentinel.md (哨兵闭环 §三) + TURN-ON-MONITORING-PLAN.md (A1-A8 监测清单)。

## 本质 (为什么是纯函数 + 统一 Observation 快照)
哨兵 = 观测→检测→处置闭环。本模块是其中**检测**那一格: 把一个**统一观测快照** (Observation,
跨设计 §3.1 五个信号源) 跑过一组**确定性纯检测规则**, 返回 SentinelFinding 清单。纯 = hermetic 可测、
无副作用 (不 emit/不 spawn/不写账本); emit 由 caller 经 commit gate (efficiency_boundary_scan 同款
emit-by-caller 纪律, 也是 self-attestation 的体现: 哨兵的发现和工人一样走 gate)。

## 检测规则 = 浅而确定 (第二道防线, 非第三道深扫)
每条规则: 单类运行时信号 → 规定 finding。不做 LLM 语义深判 (那是审查能力 Skill /reckon 第三道线的活,
故意不同源, 防同源蒙蔽)。规则做成**数据驱动的注册表** (DETECTION_RULES), 加一类信号 = 加一个函数挂表,
不改闭环 (呼应 exception_taxonomy 的可扩展)。

## push vs pull (V-02 可信监督的落点)
每条 finding 带 route: PUSH = owner 必须立即知道/可能要介入 (孪生/账本腐蚀/误杀/升级/不可逆拦);
其余 PULL = 看板。push 底下是"看得见才敢全开 autopilot"。

## level-triggered 友好
规则只看当前快照算 finding, 不 latch (不靠"捕获某事件"记忆 finding 存在)。闭合判据 = 下一轮快照重算
该 finding 不再出现 (sentinel 闭环负责), 不是这里记状态。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from .cli_run_noise import CliNoiseReport
from .contracts.usage_governor import ResourceReading
from .stress_invariants import check_static_invariants

# 血缘填充率默认阈值 (K2b-REG#3 spawn 收口后该高; 持续 0 = 收口没真生效)。
DEFAULT_LINEAGE_FILL_THRESHOLD = 0.5
# governor 近阈值默认 (与 usage_governor 一致)。
DEFAULT_BACKOFF_THRESHOLD = 0.8
# CLI 噪音比例报警阈值 (超过 = 重发/误杀在发生)。
DEFAULT_CLI_NOISE_RATIO_THRESHOLD = 0.3


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class Route(str, Enum):
    PUSH = "push"  # 主动推 owner-inbox (必须立即知道)
    PULL = "pull"  # 看板 (owner 来查)


@dataclass(frozen=True)
class SentinelFinding:
    """哨兵的一条检测发现 (一等对象候选)。caller 据此 emit canonical FindingCreated 经 gate。

    rule_id:          产它的规则 (A1..A8 / done_real / efficiency)。
    finding_kind:     治理 finding 专属类 (不劫持通用 anomaly; 设计 §3.3)。
    trigger_event_id: provenance —— evidence 它的源事件 id (满足 no_finding_misfire; 缺则 caller
                      用本轮 daemon run 事件兜底, 绝不留悬空)。
    """

    rule_id: str
    finding_kind: str
    severity: Severity
    route: Route
    detail: str
    evidence: dict = field(default_factory=dict)
    trigger_event_id: str | None = None


@dataclass(frozen=True)
class ReapDecision:
    """A2: 一次判 DEAD 并收割的裁决记录 (从判活决策日志读)。"""

    session_id: str
    reaped_at_seq: int
    evidence: str = ""  # 用的正向死亡证据 (pid 没了 / abort / 预算耗尽)


@dataclass(frozen=True)
class ReconcileCycle:
    """A3: 一轮 reconcile 的活动快照。"""

    watermark_before: int
    watermark_after: int
    dispatched_count: int
    active_session_count: int
    action_count: int = 0


@dataclass(frozen=True)
class ForwardStart:
    """A3 双驱动撞车: 一次前进链 start (plan_id, phase, 谁发的)。"""

    plan_id: str
    phase: str
    session_id: str


@dataclass(frozen=True)
class EscalationRecord:
    """A4: 一条升级 (owner-gate / 不可逆 / novel / 卡>1h)。"""

    escalation_id: str
    raised_at_seq: int
    resolved: bool
    reason: str = ""
    age_seconds: float | None = None  # 卡多久 (>1h 必 push)
    trigger_event_id: str | None = None


@dataclass(frozen=True)
class IrreversibleBlock:
    """A6: 一次物理闸位拦截 (IrreversibleActionBlocked)。"""

    risk_class: str
    detail: str
    trigger_event_id: str | None = None


@dataclass(frozen=True)
class DoneRealSignal:
    """观测源③ done-real 假信号 (verdict 门 / obligation 覆盖 / scope drift)。

    抓"假装做完" = harness 存在的理由, 哨兵盯的第一类病 (职业怀疑: 默认怀疑工人自报"做完了")。
    """

    kind: str          # "verdict_gate" / "obligation_coverage" / "scope_drift"
    target: str        # task_id / session_id / plan_id
    detail: str
    trigger_event_id: str | None = None


@dataclass(frozen=True)
class RecursiveFixSignal:
    """§3.4 递归自修观测: 同一系统模块短时间被 fix N 次 (可能死循环"修 A 引出 B")。

    owner E.5 硬决策不加链长 cap → 不靠链长门, 改用观测告警 (给原则不给死规则)。真相源 = fix 事件
    按 target 聚合的计数 (daemon 注入), 不在裸事件里硬凑。
    """

    target: str        # 被反复 fix 的系统模块 / task_id
    fix_count: int
    window_desc: str = ""  # 窗口口径 (如 "近 1h" / "近 20 轮"), 给 owner 看


@dataclass(frozen=True)
class EfficiencyFlag:
    """观测源① 效率越界 (transcript_efficiency 四口径; 探针在 l2 复用, 哨兵只消费其 flag)。"""

    session_id: str
    metric: str        # genuine_error_rate / orientation_length / reread_waste / referent_correction
    value: float
    trigger_event_id: str | None = None


@dataclass(frozen=True)
class MisrouteSignal:
    """INV-E · tw 解析 misroute (写进非 canonical 账本) 或 fail-closed 错的观测信号。

    源 pluggable-observation-seam@v1 硬核不变量 INV-E (检测完整性): tw 解析'我该写哪个账本'时
    misroute (焊到非 canonical 账本) 或 resolve fail-closed (顶端无真 canonical 拒) —— 每一次都
    必须产一条 canonical FindingCreated 全局可见, 让突发错误立刻全局定位。这是【接缝喂进观测流的
    信号载体】, 由 cli_run_feeder 的单一发射点收下, 经本模块 detect_misroute 判成 finding。
    """

    kind: str                         # "non_canonical_write" / "resolution_fail_closed"
    detail: str                       # 人读的现场描述 (给 owner 定位)
    attempted_path: str = ""          # misroute: 焊错写到的账本路径 (非 canonical)
    canonical_path: str = ""          # 若解析出真 canonical, 记它 (fail-closed 时为空)
    command: str = ""                 # 触发的 tw 命令 (定位是哪条命令走岔)
    trigger_event_id: str | None = None  # provenance 源事件 id (缺则 caller 兜底, 不留悬空)


@dataclass(frozen=True)
class Observation:
    """统一观测快照 —— 跨设计 §3.1 五个信号源 (#6.1#3 unified 观测面)。

    全字段默认空 → 测试只填它要练的那源; 生产由 daemon 合并读 base+hot 账本 + 各投影填齐。
    哨兵绝不只 grep 一段账本 (CLAUDE.md 铁律, 假阴性的根)。
    """

    # 源·安全 (A1): 原始合并账本事件 (stress_invariants 消费)
    events: tuple[dict, ...] = ()
    # 源·判活 (A2): 收割裁决 + 各会话最新活动 seq (探测收割后又活=误杀)
    reap_decisions: tuple[ReapDecision, ...] = ()
    session_activity: Mapping[str, int] = field(default_factory=dict)
    # 源·自驱 (A3): reconcile 轮活动 + 前进链 start (探测空转 / 双驱动)
    reconcile_cycles: tuple[ReconcileCycle, ...] = ()
    forward_starts: tuple[ForwardStart, ...] = ()
    # 源·升级 (A4)
    escalations: tuple[EscalationRecord, ...] = ()
    # 源⑤·资源 (A5)
    resource_readings: tuple[ResourceReading, ...] = ()
    # 源④·物理闸 (A6)
    irreversible_blocks: tuple[IrreversibleBlock, ...] = ()
    # 源·血缘 (A7)
    lineage_total_sessions: int = 0
    lineage_with_parent: int = 0
    # 源·CLI 噪音 (A8): cli_run_noise.dedup 的报告
    cli_noise: CliNoiseReport | None = None
    # 源③·done-real 假信号 (advisor 强调: 不因没 A 编号就丢)
    done_real_signals: tuple[DoneRealSignal, ...] = ()
    # 源①·效率越界 (l2 探针复用, 哨兵消费 flag)
    efficiency_flags: tuple[EfficiencyFlag, ...] = ()
    # §3.4·递归自修观测 (同一模块短时间被 fix N 次 → 可能死循环)
    recursive_fix_signals: tuple[RecursiveFixSignal, ...] = ()
    # INV-E·tw 解析 misroute/fail-closed (可插拔观测接缝喂进的信号; 单一发射点驱动本源)
    misroute_signals: tuple[MisrouteSignal, ...] = ()


# 各不变量 → (severity, route)。孪生/账本腐蚀 = 最痛 (钱+真相源), CRITICAL push 立即。
_INVARIANT_SEVERITY = {
    "no_twin": (Severity.CRITICAL, Route.PUSH),
    "no_ledger_corruption": (Severity.CRITICAL, Route.PUSH),
    "no_finding_misfire": (Severity.MAJOR, Route.PUSH),
}


def detect_a1_invariants(obs: Observation) -> list[SentinelFinding]:
    """A1 · 安全四不变量 (复用 stress_invariants 的三条可静态查; 锁死那条由 A3 空转侧面兜)。"""
    out: list[SentinelFinding] = []
    for v in check_static_invariants(list(obs.events)):
        sev, route = _INVARIANT_SEVERITY.get(v.invariant, (Severity.MAJOR, Route.PUSH))
        out.append(SentinelFinding(
            rule_id="A1", finding_kind="safety_invariant", severity=sev, route=route,
            detail=f"[{v.invariant}] {v.detail}", evidence=v.evidence,
            trigger_event_id=v.evidence.get("event_id"),
        ))
    return out


def detect_a2_misreap(obs: Observation) -> list[SentinelFinding]:
    """A2 · 误杀探测 (owner 实战#2 最痛): 被判 DEAD 收割的会话, 之后又有活动信号 = 误杀。"""
    out: list[SentinelFinding] = []
    for d in obs.reap_decisions:
        last = obs.session_activity.get(d.session_id)
        if last is not None and last > d.reaped_at_seq:
            out.append(SentinelFinding(
                rule_id="A2", finding_kind="liveness_misjudge",
                severity=Severity.CRITICAL, route=Route.PUSH,
                detail=(f"会话 {d.session_id} 在 seq {d.reaped_at_seq} 被判 DEAD 收割, "
                        f"但 seq {last} 又有活动 = 误杀 (卡死被当死)"),
                evidence={"session_id": d.session_id, "reaped_at_seq": d.reaped_at_seq,
                          "later_activity_seq": last, "reap_evidence": d.evidence},
            ))
    return out


def detect_a3_reconcile(obs: Observation) -> list[SentinelFinding]:
    """A3 · 自驱循环活动: ① 空转 (水位涨但 0 真派发 0 会话) ② 双驱动撞车 (同 plan/phase 两个 driver)。"""
    out: list[SentinelFinding] = []
    # ① 空转 (autopilot_idle_audit 教训: daemon 吃自己心跳, 水位涨却零真活)
    cycles = obs.reconcile_cycles
    if cycles:
        advanced = any(c.watermark_after > c.watermark_before for c in cycles)
        any_real = any(c.dispatched_count > 0 or c.active_session_count > 0 for c in cycles)
        if advanced and not any_real:
            out.append(SentinelFinding(
                rule_id="A3", finding_kind="reconcile_idle",
                severity=Severity.MAJOR, route=Route.PUSH,
                detail=(f"reconcile 跑了 {len(cycles)} 轮、水位线在涨, 但 0 真派发 0 活会话 "
                        f"= daemon 吃自己心跳空转"),
                evidence={"cycles": len(cycles),
                          "watermark_delta": cycles[-1].watermark_after - cycles[0].watermark_before},
            ))
    # ② 双驱动撞车 (forward-chain double-drive)
    drivers: dict[tuple[str, str], set[str]] = defaultdict(set)
    for s in obs.forward_starts:
        # 双驱动只对【真共享同一 plan_id】的会话成立。采访/共识 (及缺 plan_id 的 planner) plan_id 为空,
        # 按空串聚合会把一堆互不相干的独立会话误判成"N 个 driver 推同一 plan"(FP, 实测 70 采访塌成
        # 一条告警)。无 plan_id 无从判双驱动 → 跳过。
        if not s.plan_id:
            continue
        drivers[(s.plan_id, s.phase)].add(s.session_id)
    for (plan_id, phase), sids in drivers.items():
        if len(sids) > 1:
            out.append(SentinelFinding(
                rule_id="A3", finding_kind="forward_double_drive",
                severity=Severity.MAJOR, route=Route.PUSH,
                detail=f"plan {plan_id} 的 {phase} 被 {len(sids)} 个 driver 同时推 = 双驱动撞车",
                evidence={"plan_id": plan_id, "phase": phase, "drivers": sorted(sids)},
            ))
    return out


def detect_a4_escalations(obs: Observation) -> list[SentinelFinding]:
    """A4 · 升级 push: 每条未解决的升级 (owner-gate/不可逆/novel/卡>1h) → 主动 push main-inbox。

    R08 教训: 安全网只 pull 无 push、烂了才报 → 哨兵把它做成 push + 早报。
    """
    out: list[SentinelFinding] = []
    for e in obs.escalations:
        if e.resolved:
            continue
        stuck_long = e.age_seconds is not None and e.age_seconds > 3600  # 卡>1h
        sev = Severity.CRITICAL if stuck_long else Severity.MAJOR
        suffix = f" (卡 {e.age_seconds:.0f}s >1h)" if stuck_long else ""
        out.append(SentinelFinding(
            rule_id="A4", finding_kind="escalation", severity=sev, route=Route.PUSH,
            detail=f"未解决升级 {e.escalation_id}: {e.reason}{suffix}",
            evidence={"escalation_id": e.escalation_id, "raised_at_seq": e.raised_at_seq,
                      "age_seconds": e.age_seconds},
            trigger_event_id=e.trigger_event_id,
        ))
    return out


def detect_a5_governor(obs: Observation, *, threshold: float = DEFAULT_BACKOFF_THRESHOLD) -> list[SentinelFinding]:
    """A5 · 资源 governor: 任一资源过阈值 → 全局降率 (push); 否则不报 (停派是常规, 看板看)。"""
    out: list[SentinelFinding] = []
    over = [r for r in obs.resource_readings if r.used_fraction >= threshold]
    if over:
        worst = max(over, key=lambda r: r.used_fraction)
        out.append(SentinelFinding(
            rule_id="A5", finding_kind="resource_governor",
            severity=Severity.MAJOR, route=Route.PUSH,
            detail=(f"资源近满触发全局降率: {worst.resource} 用了 {worst.used_fraction:.0%} "
                    f"(阈值 {threshold:.0%}); 停派新活等窗口"),
            evidence={"over": [(r.resource, r.used_fraction) for r in over],
                      "resume_at_epoch": worst.resume_at_epoch},
        ))
    return out


def detect_a6_irreversible(obs: Observation) -> list[SentinelFinding]:
    """A6 · 物理闸位: 每个 IrreversibleActionBlocked (自驱撞红线被拦) → push (owner 要知道)。"""
    return [
        SentinelFinding(
            rule_id="A6", finding_kind="irreversible_block",
            severity=Severity.MAJOR, route=Route.PUSH,
            detail=f"高危动作被物理闸拦 [{b.risk_class}]: {b.detail}",
            evidence={"risk_class": b.risk_class},
            trigger_event_id=b.trigger_event_id,
        )
        for b in obs.irreversible_blocks
    ]


def detect_a7_lineage(obs: Observation, *, threshold: float = DEFAULT_LINEAGE_FILL_THRESHOLD) -> list[SentinelFinding]:
    """A7 · 血缘健康: parent_session_id 填充率低 = spawn 收口没真生效; 持续 0 → push。"""
    total = obs.lineage_total_sessions
    if total <= 0:
        return []
    fill = obs.lineage_with_parent / total
    if fill >= threshold:
        return []
    # 持续 0 填充 = 收口结构性没生效 (最严重); 否则低于阈值是退化告警。
    persistent_zero = obs.lineage_with_parent == 0
    return [SentinelFinding(
        rule_id="A7", finding_kind="lineage_gap",
        severity=Severity.MAJOR if persistent_zero else Severity.MINOR,
        route=Route.PUSH if persistent_zero else Route.PULL,
        detail=(f"血缘填充率 {fill:.0%} (<{threshold:.0%}); "
                + ("0 填充 = spawn 收口没真生效" if persistent_zero else "spawn 收口部分漏")),
        evidence={"total": total, "with_parent": obs.lineage_with_parent, "fill_rate": fill},
    )]


def detect_a8_cli_noise(obs: Observation, *, ratio_threshold: float = DEFAULT_CLI_NOISE_RATIO_THRESHOLD) -> list[SentinelFinding]:
    """A8 · CLI-RUN 噪音: 重发噪音比例超阈值 或 有归属不清残骸 → 报 (噪音复发 push)。"""
    rep = obs.cli_noise
    if rep is None or not rep.is_noisy:
        return []
    high_noise = rep.noise_ratio >= ratio_threshold
    has_ambiguous = bool(rep.ambiguous)
    # 复发 (高比例) 或 分不清谁是谁 → push; 否则低量噪音 pull 看板。
    push = high_noise or has_ambiguous
    return [SentinelFinding(
        rule_id="A8", finding_kind="cli_run_noise",
        severity=Severity.MAJOR if push else Severity.MINOR,
        route=Route.PUSH if push else Route.PULL,
        detail=(f"CLI-RUN 噪音: {len(rep.noise_runs)} 条重发 (比例 {rep.noise_ratio:.0%}), "
                f"{len(rep.ambiguous)} 条归属不清"),
        evidence={"noise_ratio": rep.noise_ratio, "noise_count": len(rep.noise_runs),
                  "ambiguous": list(rep.ambiguous)},
    )]


def detect_done_real_fakes(obs: Observation) -> list[SentinelFinding]:
    """观测源③ · done-real 假信号 (verdict 门 / obligation 覆盖 / scope drift)。

    抓"假装做完" = harness 存在的理由 (advisor 强调勿丢)。职业怀疑: 默认怀疑工人自报"做完了"。
    """
    return [
        SentinelFinding(
            rule_id="done_real", finding_kind="done_real_fake",
            severity=Severity.MAJOR, route=Route.PUSH,
            detail=f"done-real 假信号 [{s.kind}] {s.target}: {s.detail}",
            evidence={"kind": s.kind, "target": s.target},
            trigger_event_id=s.trigger_event_id,
        )
        for s in obs.done_real_signals
    ]


def detect_efficiency_boundary(obs: Observation) -> list[SentinelFinding]:
    """观测源① · 效率越界 (l2 transcript_efficiency 探针复用, 哨兵消费 flag)。

    severity gating: 默认只 surface (pull), 不淹 fix 队列 (粗探针高 FP)。
    """
    return [
        SentinelFinding(
            rule_id="efficiency", finding_kind="efficiency_regression",
            severity=Severity.MINOR, route=Route.PULL,
            detail=f"效率越界 会话 {f.session_id}: {f.metric}={f.value}",
            evidence={"session_id": f.session_id, "metric": f.metric, "value": f.value},
            trigger_event_id=f.trigger_event_id,
        )
        for f in obs.efficiency_flags
    ]


def detect_recursive_fix(obs: Observation, *, threshold: int = 3) -> list[SentinelFinding]:
    """§3.4 · 递归自修观测告警: 同一模块短时间被 fix ≥N 次 → 可能"修 A 引出 B"死循环 → push。

    不是物理链长门 (owner E.5 不加 cap), 是观测告警 (给原则不给死规则)。auto-fix dormant 时本就不会
    自修, 此规则是 turn-on 后的安全网 —— 自修开火后反复修同一处 = 死循环的早信号。
    """
    return [
        SentinelFinding(
            rule_id="recursive_fix", finding_kind="recursive_fix_warning",
            severity=Severity.MAJOR, route=Route.PUSH,
            detail=(f"模块 {s.target} {s.window_desc}被 fix {s.fix_count} 次 (≥{threshold}) "
                    f"= 可能递归自修死循环"),
            evidence={"target": s.target, "fix_count": s.fix_count, "window": s.window_desc},
        )
        for s in obs.recursive_fix_signals
        if s.fix_count >= threshold
    ]


def detect_misroute(obs: Observation) -> list[SentinelFinding]:
    """INV-E · tw 解析 misroute/fail-closed: 每个信号 → 一条 finding (账本身份走岔, 全局可见)。

    源 pluggable-observation-seam@v1 硬核不变量: tw 焊到非 canonical 账本 (misroute) 或解析
    fail-closed 拒 (顶端无真 canonical) —— 都是"写岔真相源 / 该写没写成"的账本身份错, 与账本腐蚀
    同级痛 (CRITICAL) 且 owner 要立即知道 (PUSH)。纯检测: 只把接缝喂进的信号翻成 finding, emit 由
    caller (接缝的 canonical observer) 经 commit gate 产 FindingCreated (emit-by-caller 纪律)。
    """
    return [
        SentinelFinding(
            rule_id="misroute", finding_kind="ledger_misroute",
            severity=Severity.CRITICAL, route=Route.PUSH,
            detail=(
                f"tw 解析账本身份走岔 [{s.kind}]: {s.detail}"
                + (f" (焊错写到 {s.attempted_path})" if s.attempted_path else "")
                + (f" [命令 {s.command}]" if s.command else "")
            ),
            evidence={"kind": s.kind, "attempted_path": s.attempted_path,
                      "canonical_path": s.canonical_path, "command": s.command},
            trigger_event_id=s.trigger_event_id,
        )
        for s in obs.misroute_signals
    ]


# 检测规则注册表 (数据驱动: 加一类信号 = 加一行, 不改 sentinel 闭环)。
DETECTION_RULES = (
    detect_a1_invariants,
    detect_a2_misreap,
    detect_a3_reconcile,
    detect_a4_escalations,
    detect_a5_governor,
    detect_a6_irreversible,
    detect_a7_lineage,
    detect_a8_cli_noise,
    detect_done_real_fakes,
    detect_efficiency_boundary,
    detect_recursive_fix,
    detect_misroute,
)


def run_all_detections(obs: Observation) -> list[SentinelFinding]:
    """跑所有检测规则, 汇总 finding。纯函数, 无副作用。"""
    return [f for rule in DETECTION_RULES for f in rule(obs)]
