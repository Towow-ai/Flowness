"""哨兵⑥ · 观测→检测→finding→可逆性分诊→(自动修/escalate)→再观测 闭环核 (设计 §三)。

这是把五条张力的解组装成一条**可建造的闭合环**。它是**环**不是**线** ("再观测"必回起点),
level-triggered (反复对比期望vs实际, 漏一轮下轮收敛), 每条非顺利路有规定动作。

## emit-by-caller 纯函数 (self-attestation 的体现, T6.2)
本核**只算**一轮的 finding + 处置, 不 emit/不 spawn/不写账本 (hermetic 可测)。canonical FindingCreated
经 commit gate 由 caller (daemon/orchestrator) emit —— 哨兵的发现和工人造的假**同一套抓假机制审**。

## 可逆性分诊 = write_set ∩ 高危类 (机器判, 非 LLM, T6.3)
finding 的修复动作可逆性**不靠 LLM 语义猜** (会被"这只是清临时文件"说服), 靠层④物理闸位判写集触没触
高危类。两段: (a) 本核按 finding 带的 fix 预估写集做**粗判** (可逆/待层④终判); (b) 自动修 agent 真写时
由层④物理闸位**终判** (碰高危 → 物理拦 → IrreversibleActionBlocked → 转 escalate)。本核不替物理闸做判断。

## 自动修 / 自驱全 DORMANT (flag-off, INF-003 owner-gate)
`auto_repair_unfrozen=False` (缺省) → **没有任何 finding 被标 AUTO_FIX_CANDIDATE**, 全 surface-only
(只 emit finding 给 owner, 绝不真自动改东西)。可逆+TRUSTED+解冻 才进自动派修候选 —— 即便那样本核也只
**标候选**, 真派由 caller orchestrator (本核不 spawn)。这是 turn-on 等 owner 的物理保守默认。

## verify-the-observer 喂处置 (T6.2 degradation 非 shutdown)
哨兵自己这轮可信度 (AttestationVerdict) 喂分诊: verdict 不可信 (DEGRADED/UNVALIDATED) → 即便可逆+解冻
也降回 surface-only (没达标的哨兵不行使自动派修)。不可信 ≠ 停摆 (照常观测+surface, 治理环路不全黑)。

## level-triggered 闭合 (T6.3 必要条件)
finding 闭合判据 = **下一轮再观测重算它不再出现** (期望 vs 实际), **绝不是 FixCompleted 就信**。
caller 传上轮 finding 签名集; 本轮算出的签名集里没了的 = 真闭合 (再观测确认消失)。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from .contracts.physical_gate import ActionContext, HighRiskClass, PhysicalGate
from .detection_rules import Observation, Route, SentinelFinding, run_all_detections
from .verify_observer import AttestationStatus, AttestationVerdict


class DispositionKind(str, Enum):
    SURFACE_ONLY = "surface_only"            # 只 emit finding 给 owner (dormant / 降级 / pull)
    AUTO_FIX_CANDIDATE = "auto_fix_candidate"  # AI 更擅长 (软件/内部) → 自动派修候选 (本核只标, caller 真派)
    ESCALATE = "escalate"                    # 需 owner 的想法 / 动钱硬停 → push owner (层④物理闸位终判兜)


@dataclass(frozen=True)
class DispositionedFinding:
    finding: SentinelFinding
    disposition: DispositionKind
    reversible: bool | None  # True/False=粗判; None=本核未判, 待层④写时终判
    reason: str


@dataclass(frozen=True)
class SentinelReport:
    """一轮哨兵闭环的产出 (纯; caller 据此 emit + 决定派不派)。"""

    cycle_findings: tuple[DispositionedFinding, ...]
    closed_findings: tuple[str, ...]          # 上轮在、本轮再观测确认消失的 finding 签名
    push: tuple[SentinelFinding, ...]          # 必须主动推 owner-inbox 的 (只含 to_emit 新发现)
    pull: tuple[SentinelFinding, ...]          # 看板的 (只含 to_emit 新发现)
    attestation: AttestationStatus             # 哨兵自己这轮可信度
    auto_repair_unfrozen: bool                  # dormant 状态明示 (False=全 surface)
    signatures: frozenset[str] = field(default_factory=frozenset)  # 本轮【全部】检测到的 finding 签名 (喂下轮闭合判定)
    # f-escalation-task-oriented 去重 (caller 只 emit to_emit): 已报开着的不重报, ACCEPTED 永不报。
    suppressed_open: tuple[SentinelFinding, ...] = ()       # 已报过且开着 → 去重 (不重 emit)
    suppressed_accepted: tuple[SentinelFinding, ...] = ()   # 已 ACCEPTED (改不了/不可变如 553) → 永不报


def finding_signature(f: SentinelFinding) -> str:
    """finding 稳定签名 (level-triggered 闭合用): 同一问题反复观测产同一签名。

    取 (rule_id, finding_kind, evidence 的稳定锚字段), 不含会变的措辞/计数, 这样"同一个孪生"
    每轮签名一致 → 修好后它从签名集消失 = 闭合。

    F-1 修复 (签名塌缩活血案): `event_id` 加入白名单 —— 之前 no_finding_misfire 的 evidence 只有
    `event_id`/`trigger` 两个键, 都不在白名单里, 锚点恒为 `{}` → 1592 条互不相同的真实事件全部
    塌成同一个签名 (f-sentinel-1fd34afde707b3c9), 修好检测器本身也救不回来 (信息已经在签名这层丢了,
    accept 一次等于永久闭嘴)。`event_id` 是账本里唯一保证逐条不同的字段, 加入后不同实例天然产生
    不同签名。**向后兼容确认**: 全仓 grep 现有 8 条检测规则 (A1-A8/done_real/efficiency/
    recursive_fix/misroute) 的 evidence 构造, 没有任何一条已用 `event_id` 作 key —— 本次改动对
    它们的签名 byte-for-byte 不变, 已 accept 的历史签名 (如 A6 那条) 不受影响。
    """
    anchor = {k: f.evidence[k] for k in sorted(f.evidence)
              if k in ("task_id", "plan_id", "session_id", "escalation_id", "seq",
                       "risk_class", "target", "phase", "metric", "event_id")}
    raw = json.dumps([f.rule_id, f.finding_kind, anchor], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# f-escalation-task-oriented-not-reversibility-framework — 哨兵 finding 的确定性 canonical id:
# caller emit FindingCreated 时用 finding_id = f-sentinel-<signature>, 下轮从 finding_lifecycle
# 投影按这个 id 反查"已报开着的 / ACCEPTED", 重建 open/accepted 签名集喂回闭环 → 去重 / 永不再报。
# 签名稳定 (同问题每轮同签名) ⇒ id 稳定 ⇒ round-trip 命中。
_SENTINEL_FINDING_ID_PREFIX = "f-sentinel-"


def sentinel_finding_id(signature: str) -> str:
    """哨兵 finding 的确定性 canonical finding_id (供 caller emit FindingCreated)。"""
    return f"{_SENTINEL_FINDING_ID_PREFIX}{signature}"


def signature_from_finding_id(finding_id: str) -> str | None:
    """从 f-sentinel-<sig> 反解出签名; 非哨兵 finding_id → None (caller 建 open/accepted 集用)。"""
    if finding_id.startswith(_SENTINEL_FINDING_ID_PREFIX):
        return finding_id[len(_SENTINEL_FINDING_ID_PREFIX):]
    return None


# 外部承诺类高危 (上线/对外发布/删数据/公开承诺) = owner-domain: 需 owner 的方向/意图, 不是 AI 能
# 自己拍的。动钱 (FUNDS) 单列为唯一【物理硬停】(必停必人工)。这是任务导向模型的机器判别面。
_OWNER_DOMAIN_CLASSES = frozenset({
    HighRiskClass.PROD_DEPLOY,
    HighRiskClass.OUTBOUND_PUBLISH,
    HighRiskClass.DELETE_DATA,
    HighRiskClass.PUBLIC_COMMITMENT,
})


def _fix_high_risk_class(f: SentinelFinding, gate: PhysicalGate | None) -> HighRiskClass | None:
    """粗判 finding 的修复动作触哪个高危类 (机器判, 非 LLM): fix 预估写集 + 可选 fix_command。

    无写集/命令提示或无 gate → None (本核不臆断, 待层④自动修真写时终判)。
    """
    write_set = tuple(f.evidence.get("fix_write_set", ()))
    command = str(f.evidence.get("fix_command", "") or "")
    if (not write_set and not command) or gate is None:
        return None
    return gate.classify(ActionContext(tool_name="", command_or_payload=command, write_set=write_set))


def _coarse_reversibility(risk: HighRiskClass | None, *, classifiable: bool) -> bool | None:
    """可逆性【仅作观测留存】(不再是 escalate 判据 — f-escalation-task-oriented)。

    无写集/命令可判 → None; 否则 触高危类=不可逆(False) / 没触=可逆(True)。
    """
    if not classifiable:
        return None
    return risk is None


def _needs_owner_judgment(f: SentinelFinding, risk: HighRiskClass | None) -> bool:
    """这任务需不需要 owner 的脑子 (方向/品味/意图)?

    显式 evidence flag needs_owner_judgment (产品方向/取舍/品味类 finding 自报), 或修复触外部承诺类
    高危 (上线/对外发布/删数据/公开承诺 = owner-domain, 需 owner 的意图)。其余 (软件/内部修复) AI
    自己拍, 不去烦 owner。
    """
    if f.evidence.get("needs_owner_judgment"):
        return True
    return risk in _OWNER_DOMAIN_CLASSES


def disposition_for(
    f: SentinelFinding,
    *,
    gate: PhysicalGate | None,
    auto_repair_unfrozen: bool,
    verdict_trusted: bool,
) -> DispositionedFinding:
    """任务导向处置 (f-escalation-task-oriented-not-reversibility-framework, owner 系统级原则)。

    不预设"可逆/不可逆"框架 —— 对每个问题只问"这任务需不需要 owner 的想法":
      1. 动钱 (FUNDS) = 唯一【物理硬停】→ ESCALATE (必停必人工, 层④物理闸位终判兜)。
      2. 需 owner 方向/品味/意图 (显式 flag / 外部承诺类高危) → ESCALATE (渐进:
         Advisor→interview 厘清→owner-as-input, 非二元"停下问 owner")。
      3. dormant (INF-003 owner-gate) / 哨兵 verdict 未达标 → SURFACE_ONLY (只观测留痕)。
      4. 其余 = AI 更擅长的 (软件/内部修复) → AUTO_FIX_CANDIDATE (推动修, 哪怕'不可逆' ——
         可逆性不是判据; 本核只标候选, 真派由 caller orchestrator 经原子认领)。
    reversible 字段仅作【观测】留存, 不再是 escalate 判据。
    """
    classifiable = bool(f.evidence.get("fix_write_set") or f.evidence.get("fix_command")) and gate is not None
    risk = _fix_high_risk_class(f, gate)
    reversible = _coarse_reversibility(risk, classifiable=classifiable)
    # 1. 动钱 = 唯一物理硬停 (即便 dormant 也要 surface 给 owner)
    if risk is HighRiskClass.FUNDS:
        return DispositionedFinding(f, DispositionKind.ESCALATE, reversible,
                                    "动钱级资金外部性 = 唯一物理硬停 → escalate (必停必人工, 层④物理闸位终判)")
    # 2. 需 owner 的方向/品味/意图 → escalate (渐进, owner 当素材之一)
    if _needs_owner_judgment(f, risk):
        return DispositionedFinding(f, DispositionKind.ESCALATE, reversible,
                                    "需 owner 的方向/品味/意图 → escalate (渐进 Advisor→interview→owner-as-input)")
    # 3. DORMANT 或 哨兵未达标 → surface-only (绝不自动修)
    if not auto_repair_unfrozen:
        return DispositionedFinding(f, DispositionKind.SURFACE_ONLY, reversible,
                                    "自动修 dormant (INF-003 owner-gate) → 只观测留痕")
    if not verdict_trusted:
        return DispositionedFinding(f, DispositionKind.SURFACE_ONLY, reversible,
                                    "哨兵 verdict 未达标 (verify-the-observer 降级) → surface-only")
    # 4. AI 更擅长 (软件/内部修复) → 自动派修候选 (哪怕'不可逆' —— 可逆性不是判据)
    return DispositionedFinding(f, DispositionKind.AUTO_FIX_CANDIDATE, reversible,
                                "AI 更擅长(软件/内部)→ 自动派修候选 (哪怕'不可逆'; caller 经原子认领派, 层④兜)")


def run_sentinel_cycle(
    obs: Observation,
    *,
    gate: PhysicalGate | None = None,
    attestation: AttestationVerdict | None = None,
    auto_repair_unfrozen: bool = False,
    prev_signatures: frozenset[str] = frozenset(),
    open_signatures: frozenset[str] = frozenset(),
    accepted_signatures: frozenset[str] = frozenset(),
) -> SentinelReport:
    """跑一轮哨兵闭环 (纯函数)。observe(传入快照)→detect→生命周期去重→分诊→push/pull 分流→算闭合。

    attestation=None → 视作 UNVALIDATED (verdict 不可信), 保守默认 (没核对过的哨兵不准自动派修)。
    auto_repair_unfrozen=False (缺省) → 全 surface-only (dormant)。

    f-escalation-task-oriented-not-reversibility-framework 生命周期去重 (caller 从 finding_lifecycle
    投影按 finding_id=f-sentinel-<sig> 反查后传入):
      - accepted_signatures: 已 ACCEPTED (改不了/不可变如账本 553) → 永不报 (suppressed_accepted)。
      - open_signatures:     已报过且仍开着 (CREATED/VERIFIED/DISPUTED) → 不重报 (suppressed_open)。
      - 其余 = to_emit (真新发现): 只对它分诊 + push/pull, caller 只 emit 它。
    🔴 闭合判定 (signatures/closed) 用【本轮全部检测到的】, 不减去 accepted/open —— 这样 ACCEPTED
    的问题每轮都被检测到 ⇒ 一直在 signatures ⇒ 永不误判"闭合"、也永不进 to_emit = 正好"永不再报"。
    缺省两集为空 → to_emit = 全部 ⇒ 与旧行为 byte 兼容 (无去重)。
    """
    status = attestation.status if attestation is not None else AttestationStatus.UNVALIDATED
    verdict_trusted = attestation.verdict_trusted if attestation is not None else False

    findings = run_all_detections(obs)
    # 闭合永远基于【本轮全部】检测到的签名 (与去重正交)。
    signatures = frozenset(finding_signature(f) for f in findings)
    closed = tuple(sorted(prev_signatures - signatures))

    # 生命周期去重: 按签名把本轮检测分三桶 (ACCEPTED 永不报 > 已开着不重报 > 新发现 to_emit)。
    to_emit: list[SentinelFinding] = []
    suppressed_open: list[SentinelFinding] = []
    suppressed_accepted: list[SentinelFinding] = []
    for f in findings:
        sig = finding_signature(f)
        if sig in accepted_signatures:
            suppressed_accepted.append(f)
        elif sig in open_signatures:
            suppressed_open.append(f)
        else:
            to_emit.append(f)

    # 分诊 / push-pull 只对 to_emit (新发现); 已报/已 accept 的不再行动、不再刷屏。
    dispositioned = tuple(
        disposition_for(f, gate=gate, auto_repair_unfrozen=auto_repair_unfrozen,
                        verdict_trusted=verdict_trusted)
        for f in to_emit
    )
    push = tuple(f for f in to_emit if f.route is Route.PUSH)
    pull = tuple(f for f in to_emit if f.route is Route.PULL)

    return SentinelReport(
        cycle_findings=dispositioned,
        closed_findings=closed,
        push=push,
        pull=pull,
        attestation=status,
        auto_repair_unfrozen=auto_repair_unfrozen,
        signatures=signatures,
        suppressed_open=tuple(suppressed_open),
        suppressed_accepted=tuple(suppressed_accepted),
    )
