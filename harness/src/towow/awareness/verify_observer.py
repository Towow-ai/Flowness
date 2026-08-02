"""哨兵⑥ · verify-the-observer + self-attestation gate (设计 §四 / T6.1 / T6.2)。

"谁观测观测者" —— 哨兵最承重、最容易做假的部件。哨兵自报"我准"零价值 (观测层自己就是那个把
genuine error 低估了四成的探针)。本模块把"独立核对哨兵 + 哨兵自己别骗自己"落成机制。

## 两层独立 + 在算法独立层截断递归 (T6.1)
1. **结构层 (时间独立, 不需 fork)**: 用**同一份纯函数**对 raw 账本重算, 跟哨兵观测时用的快照比。
   ⚠ 诚实边界 (设计 §4.1): 故意复用同一函数 → 它**只抓投影 staleness/腐蚀/账本视图不全** (如只
   grep 一段=假阴), **不抓检测逻辑本身的盲区**。后者要换判断范式 (语义层) 才抓得到。别把结构层
   framing 成"验证了哨兵判断对" —— 它验的是"哨兵看的数据没烂", 不是"哨兵的逻辑对"。
2. **语义层 (算法独立, 截断点)**: 派看不到探针实现、用 LLM 语义 (而非第二份正则) 复判的 fork。
   这才是真独立性。**DORMANT**: spawn 缺省不注入 → 结构上不起 fork (设计 §4.5 死分支: 解冻后
   caller 注入 spawn 才真跑)。fail-closed: 注入但失败/超时 → 绝不静默放行假装核对过。

递归截断在语义层, 靠两个**非递归兜底**接残余: 每条 discrepancy 带 rationale (证据可被 owner 直接复核) +
owner 终审 (哨兵永不自动宣布"我准了"作系统级结论)。第三层若还是 LLM-as-judge = 同源蒙蔽, 增本不增独立。

## self-attestation = degradation 而非 shutdown (T6.2)
哨兵没达标 → 退回 surface-only (owner 看得见但不自动派修), 不是停摆 (没哨兵比有个不完美的更糟)。
达标判据 = 运行时行为信号 (结构零 drift + 标定靶 FP/FN < 阈值), **不是静态代码完成度** (硬门会逼人
作弊标满分 = MEMORY"别把 built+tested 当 enforced"那条病)。**没标定数据 → UNVALIDATED, 默认降级**
(绝不无标定就自报达标)。

## 诚实状态 (本模块)
hermetic 纯函数核, **非 live 验证过**: 标定靶数据 (R07 §3 六类 4877 error) 在本 worktree 不可达 →
evaluate_against_calibration 是**机制 built / 数据 dormant** (cases 必须 caller 注入, 本模块绝不造数据)。
语义层 spawn 全程 dormant。报告口径全程 "built + hermetic-tested, dormant", 不声称 live 抓到了什么。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from .stress_invariants import check_static_invariants

# Anthropic <1% FP 的复刻; FN 阈值是标定策略 (设计未给死数, 留可调默认)。
DEFAULT_FP_THRESHOLD = 0.01
DEFAULT_FN_THRESHOLD = 0.05


# ============ 结构层: 投影 drift / 账本视图不全 (非检测逻辑验证) ============
@dataclass(frozen=True)
class DriftRecord:
    kind: str       # "missing_events" / "invariant_set_mismatch"
    detail: str
    evidence: dict = field(default_factory=dict)


def detect_observation_drift(
    observed_events: Sequence[dict], ground_truth_events: Sequence[dict]
) -> list[DriftRecord]:
    """结构层核对: 哨兵观测用的事件视图 (observed) vs 独立重读的真相 (ground_truth)。

    抓两类**数据烂** (非逻辑错): ① 观测视图漏了真相里的事件 (只 grep base 漏 hot = 假阴的活样本);
    ② 同一份不变量纯函数在两个视图上算出不同违规集 (= 观测视图被污染/过期)。
    """
    out: list[DriftRecord] = []

    def _ids(evs: Sequence[dict]) -> set[str]:
        return {e.get("event_id") for e in evs if e.get("event_id")}

    obs_ids, gt_ids = _ids(observed_events), _ids(ground_truth_events)
    missing = gt_ids - obs_ids
    if missing:
        out.append(DriftRecord(
            "missing_events",
            f"观测视图漏了 {len(missing)} 条真相里的事件 (账本视图不全, 假阴风险)",
            {"missing_count": len(missing), "sample": sorted(missing)[:10]},
        ))

    # 同一函数两视图: 违规集应一致 (不一致 = 观测视图脏)
    obs_v = {(v.invariant, v.detail) for v in check_static_invariants(list(observed_events))}
    gt_v = {(v.invariant, v.detail) for v in check_static_invariants(list(ground_truth_events))}
    if obs_v != gt_v:
        out.append(DriftRecord(
            "invariant_set_mismatch",
            "同一不变量函数在 观测视图 vs 真相 上算出不同违规集 = 观测视图过期/被污染",
            {"only_in_observed": len(obs_v - gt_v), "only_in_truth": len(gt_v - obs_v)},
        ))
    return out


# ============ 风险加权抽样 (替 seed=0 固定均匀抽的覆盖偏置, §4.4) ============
@dataclass(frozen=True)
class SamplingCandidate:
    session_id: str
    flagged_boundary: bool = False   # 越界会话
    touched_high_risk: bool = False  # 触发过高危 write 门
    new_finding_kind: bool = False   # 新出现的 finding_kind
    cycles_since_sampled: int = 0     # 距上次被抽样多少轮 (久未抽优先, 防永远漏同一批)


def risk_weighted_sample(candidates: Sequence[SamplingCandidate], n: int) -> tuple[str, ...]:
    """风险导向抽样 (审计学): 越界/高危/新类/久未抽 优先进昂贵语义复判样本。

    **确定性**排序 (风险分降序 + cycles_since_sampled 降序 + session_id 稳定 tie-break) →
    不依赖随机种子, 不会像 seed=0 那样永远抽到同一批、把新越界永久漏掉 (设计 §T6.5 塌点修复)。
    """
    if n <= 0:
        return ()

    def score(c: SamplingCandidate) -> tuple:
        risk = (3 if c.touched_high_risk else 0) + (2 if c.flagged_boundary else 0) \
               + (1 if c.new_finding_kind else 0)
        # 降序: 高风险先、久未抽先; 末位 session_id 升序稳定 tie-break
        return (-risk, -c.cycles_since_sampled, c.session_id)

    return tuple(c.session_id for c in sorted(candidates, key=score)[:n])


# ============ 标定靶: 量观测分类器 FP/FN (验证器准不准 = 系统级假设) ============
@dataclass(frozen=True)
class CalibrationCase:
    text: str
    expected_label: str  # 人工标过的已知答案


@dataclass(frozen=True)
class CalibrationResult:
    total: int
    false_positive: int
    false_negative: int

    @property
    def fp_rate(self) -> float:
        return self.false_positive / self.total if self.total else 0.0

    @property
    def fn_rate(self) -> float:
        return self.false_negative / self.total if self.total else 0.0


def evaluate_against_calibration(
    classifier: Callable[[str], str],
    cases: Sequence[CalibrationCase],
    *,
    positive_label: str,
) -> CalibrationResult:
    """喂标定靶 (注入的已知答案集) 给被验分类器, 量 FP/FN (owner 纪律7: 昂贵标定花在"验证器准不准")。

    classifier: 被验的观测分类器 (注入, 不耦合具体实现)。
    cases:      人工标注集 (R07 §3 六类做种子)。**本函数绝不内置任何标定数据** —— cases 由 caller
                注入真标注集; 空集 → total=0 (caller 据此判 UNVALIDATED, 不能冒充"标定过")。
    positive_label: 视为"阳性"(如 genuine error) 的标签; FP = 判阳实阴, FN = 判阴实阳。
    """
    fp = fn = 0
    for c in cases:
        predicted_pos = classifier(c.text) == positive_label
        actual_pos = c.expected_label == positive_label
        if predicted_pos and not actual_pos:
            fp += 1
        elif not predicted_pos and actual_pos:
            fn += 1
    return CalibrationResult(total=len(cases), false_positive=fp, false_negative=fn)


# ============ self-attestation gate: TRUSTED / DEGRADED / UNVALIDATED ============
class AttestationStatus(str, Enum):
    TRUSTED = "trusted"          # 结构零 drift + 标定达标 → verdict 可进自动派修 (解冻后)
    DEGRADED = "degraded"        # drift 或 标定超阈 → 降级 surface-only (非 shutdown)
    UNVALIDATED = "unvalidated"  # 无标定数据 → 不能宣称达标, 默认 surface-only


@dataclass(frozen=True)
class AttestationVerdict:
    status: AttestationStatus
    reason: str
    drift_count: int
    calibration: CalibrationResult | None

    @property
    def verdict_trusted(self) -> bool:
        """verdict 能否被采信去自动派修 (只有 TRUSTED; 其余降级仅 surface 给 owner)。"""
        return self.status is AttestationStatus.TRUSTED


def attestation_gate(
    drift: Sequence[DriftRecord],
    calibration: CalibrationResult | None,
    *,
    fp_threshold: float = DEFAULT_FP_THRESHOLD,
    fn_threshold: float = DEFAULT_FN_THRESHOLD,
) -> AttestationVerdict:
    """self-attestation (T6.2): 达标 = 结构零 drift + 标定 FP/FN < 阈值 → TRUSTED;
    否则 DEGRADED (surface-only, 非 shutdown)。无标定 → UNVALIDATED (默认降级, 不自报达标)。
    """
    drift_count = len(drift)
    if calibration is None or calibration.total == 0:
        return AttestationVerdict(
            AttestationStatus.UNVALIDATED,
            "无标定数据 → 不能宣称达标, verdict 降级 surface-only (绝不无标定自报我准了)",
            drift_count, calibration,
        )
    if drift_count > 0:
        return AttestationVerdict(
            AttestationStatus.DEGRADED,
            f"结构层 {drift_count} 条 drift (观测视图烂) → verdict 降级 surface-only",
            drift_count, calibration,
        )
    if calibration.fp_rate >= fp_threshold or calibration.fn_rate >= fn_threshold:
        return AttestationVerdict(
            AttestationStatus.DEGRADED,
            (f"标定超阈 (FP={calibration.fp_rate:.2%}≥{fp_threshold:.0%} 或 "
             f"FN={calibration.fn_rate:.2%}≥{fn_threshold:.0%}) → verdict 降级 surface-only"),
            drift_count, calibration,
        )
    return AttestationVerdict(
        AttestationStatus.TRUSTED,
        "结构零 drift + 标定达标 → verdict 可信 (解冻后可进自动派修)",
        drift_count, calibration,
    )


# ============ 语义层 fork (算法独立的真独立性) —— DORMANT ============
@dataclass(frozen=True)
class SemanticVerifyResult:
    ran: bool
    discrepancies: tuple[dict, ...]  # 每条带 rationale (证据 ground-truth 兜底)
    reason: str


def run_semantic_verify(
    samples: Sequence[dict],
    *,
    spawn: Callable[[Sequence[dict]], tuple[dict, ...]] | None = None,
) -> SemanticVerifyResult:
    """语义层复判 (换判断范式: LLM 读 error_text 真语义, 不是第二份正则)。

    **DORMANT** (设计 §4.5 死分支接线): spawn 缺省 None → 结构上不起 fork, 返 ran=False
    (解冻 + owner 受控接线时, caller 注入 spawn 才真跑)。
    **fail-closed**: spawn 注入但抛错/超时 → 绝不静默放行假装核对过, 返 ran=False + reason。
    """
    if spawn is None:
        return SemanticVerifyResult(False, (), "semantic layer dormant: no spawn injected (INF-003)")
    try:
        discrepancies = tuple(spawn(samples))
    except Exception as exc:  # noqa: BLE001 — fail-closed: 任何 fork 失败都不放行
        return SemanticVerifyResult(False, (), f"semantic fork failed → fail-closed (不放行): {exc!r}")
    return SemanticVerifyResult(True, discrepancies, "semantic verify ran")
