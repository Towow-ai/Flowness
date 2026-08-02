"""M-1.5 §7.2 author-time aggregation — 多 fork 同根因去重 (RUN-035 T-L1-56).

author-time mode 按 review_plan.dimensions 并行调 method-execution-path / method-consistency /
method-red-team fork, 每个产 findings_proposal。多个 fork 可能对**同一根因**各报一条 finding
(如 execution-path 和 red-team 都指向同一行的并发问题)。aggregation 在 emit FindingCreated 之前
**一次性去重** (M-1.5b Patch a: 主 session 内 subagent 调用, 非独立 fork)。

这是确定性 helper (非 LLM 判断) — 同根因判据 = (risk_surface, target.artifact, target.location)
三元组相同。合并时保留最高 severity, 合并 review_dimension/description, 累计来源 fork。
去重报告可审计 (哪些被合并到哪条)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# severity 排序 (高→低): 决定合并后保留哪条的 severity。purple 是 preexisting, 排在 observation 之上。
_SEVERITY_RANK: dict[str, int] = {
    "critical": 5,
    "major": 4,
    "minor": 3,
    "purple": 2,
    "observation": 1,
}


@dataclass
class AggregatedFinding:
    """去重后的一条 finding (可能由多条同根因 proposal 合并而来)。"""

    finding_id: str
    risk_surface: str
    target_artifact: str
    target_location: str
    severity: str
    description: str
    review_dimensions: list[str] = field(default_factory=list)
    merged_from: list[str] = field(default_factory=list)  # 被合并进来的 finding_id (含自己)


def _root_cause_key(proposal: dict[str, object]) -> tuple[str, str, str]:
    """同根因判据: (risk_surface, target.artifact, target.location)。"""
    risk_surface = str(proposal.get("risk_surface", ""))
    target = proposal.get("target")
    artifact = location = ""
    if isinstance(target, dict):
        artifact = str(target.get("artifact", ""))
        location = str(target.get("location", ""))
    return (risk_surface, artifact, location)


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def aggregate_findings(
    proposals: list[dict[str, object]],
) -> tuple[list[AggregatedFinding], dict[str, list[str]]]:
    """去重多 fork 同根因 finding proposal。

    Args:
        proposals: 各方法论 fork 产的 findings_proposal 拼接 (每条含 finding_id / risk_surface /
            target:{artifact,location} / severity / description / review_dimension)。

    Returns:
        (deduped, dedup_report):
          deduped — 去重后的 AggregatedFinding 列表 (同根因合并成一条, 保留最高 severity,
                    累计 review_dimensions + merged_from)。顺序 = 各根因首次出现顺序 (确定性)。
          dedup_report — {kept_finding_id: [被合并掉的 finding_id, ...]} (审计: 谁并进谁)。
    """
    by_key: dict[tuple[str, str, str], AggregatedFinding] = {}
    order: list[tuple[str, str, str]] = []
    report: dict[str, list[str]] = {}

    for p in proposals:
        fid = str(p.get("finding_id", ""))
        key = _root_cause_key(p)
        dim = p.get("review_dimension")
        dim_str = str(dim) if dim is not None else ""
        severity = str(p.get("severity", "observation"))
        existing = by_key.get(key)
        if existing is None:
            artifact, location = key[1], key[2]
            agg = AggregatedFinding(
                finding_id=fid,
                risk_surface=key[0],
                target_artifact=artifact,
                target_location=location,
                severity=severity,
                description=str(p.get("description", "")),
                review_dimensions=[dim_str] if dim_str else [],
                merged_from=[fid],
            )
            by_key[key] = agg
            order.append(key)
            report[fid] = []
        else:
            # 同根因 → 合并进 existing
            existing.merged_from.append(fid)
            report[existing.finding_id].append(fid)
            if dim_str and dim_str not in existing.review_dimensions:
                existing.review_dimensions.append(dim_str)
            # 保留最高 severity
            if _severity_rank(severity) > _severity_rank(existing.severity):
                existing.severity = severity

    deduped = [by_key[k] for k in order]
    return deduped, report


# ════════════════════════════════════════════════════════════════════════════════
#  T-LND-07 fail-wins-fork-aggregation@v1 (INV-B2-1) — 多 fork 验证结论聚合
#
#  上面的 aggregate_findings 是 finding 去重 (同根因合并)。这里是另一回事: 同一 patch 起 N 个
#  独立验证 fork, 各返回一个 pass/fail 结论, 聚合按"任一 verified-FAIL 否决"做单调归约 —— 绝不
#  "谁先返回抓谁"(first-wins/race)。聚合是确定函数 (同输入集同输出, order-independent)。
# ════════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ForkAggregateResult:
    """多 fork 结论聚合结果。verdict ∈ passed/failed/pending。"""

    verdict: str
    total: int
    passed_count: int
    failed_count: int
    failing_fork_refs: list[str] = field(default_factory=list)


def _fork_passed(v: object) -> bool:
    """从 fork 结论取 passed bool: dict{'passed'} / 带 .passed 属性的对象 (ForkVerdict)。缺 → False
    (fail-closed: 拿不到明确 passed 视作未通过, 不放行)。"""
    if isinstance(v, dict):
        return bool(v.get("passed", False))
    return bool(getattr(v, "passed", False))


def _fork_ref(v: object) -> str:
    if isinstance(v, dict):
        return str(v.get("fork_id") or v.get("fork_name") or v.get("fork_ref") or "?")
    return str(getattr(v, "fork_name", None) or getattr(v, "fork_id", None) or "?")


def aggregate_fork_verdicts(fork_verdicts: list[object]) -> ForkAggregateResult:
    """INV-B2-1: 对 fork 结论集做 FAIL-wins 单调归约 —— 存在任一 failed → failed; 全 passed →
    passed。order-independent (不取先到者)。空集 → passed (无否决)。"""
    failing = [_fork_ref(v) for v in fork_verdicts if not _fork_passed(v)]
    passed_count = len(fork_verdicts) - len(failing)
    verdict = "failed" if failing else "passed"
    return ForkAggregateResult(
        verdict=verdict,
        total=len(fork_verdicts),
        passed_count=passed_count,
        failed_count=len(failing),
        failing_fork_refs=failing,
    )


def converge_fork_verdicts(
    returned_verdicts: list[object], *, expected_count: int, timed_out: bool
) -> ForkAggregateResult:
    """多 fork 收敛 gate: 等所有 fork 返回 (或超时) 再 FAIL-wins 判, 不取先到者。

    未全返回且未超时 → pending (继续等 —— 绝不因某个 pass 先到就放行); 全返回 OR 超时 → 对
    已返回的 fork 结论做 FAIL-wins 聚合 (超时也只对已返回判, 不凭空强判 fail)。
    """
    all_returned = len(returned_verdicts) >= expected_count
    if not all_returned and not timed_out:
        return ForkAggregateResult(
            verdict="pending",
            total=len(returned_verdicts),
            passed_count=sum(1 for v in returned_verdicts if _fork_passed(v)),
            failed_count=sum(1 for v in returned_verdicts if not _fork_passed(v)),
            failing_fork_refs=[_fork_ref(v) for v in returned_verdicts if not _fork_passed(v)],
        )
    return aggregate_fork_verdicts(returned_verdicts)


__all__ = [
    "AggregatedFinding",
    "ForkAggregateResult",
    "aggregate_findings",
    "aggregate_fork_verdicts",
    "converge_fork_verdicts",
]
