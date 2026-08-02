"""T-L1-56 (RUN-035) — author-time aggregation 同根因去重 (确定性 helper 单测)."""

from __future__ import annotations

from towow.l1.review_aggregation import aggregate_findings


def _p(fid: str, rs: str, artifact: str, loc: str, sev: str, dim: str) -> dict[str, object]:
    return {
        "finding_id": fid,
        "risk_surface": rs,
        "target": {"artifact": artifact, "location": loc},
        "severity": sev,
        "description": f"{fid} desc",
        "review_dimension": dim,
    }


def test_same_root_cause_merged() -> None:
    """两 fork 对同一 (risk_surface,artifact,location) 各报一条 → 合并成一条."""
    proposals = [
        _p("f1", "concurrency", "gate.py", "gate.py:838", "major", "method-execution-path"),
        _p("f2", "concurrency", "gate.py", "gate.py:838", "critical", "method-red-team"),
        _p("f3", "consistency", "doc.md", "doc.md:10", "minor", "method-consistency"),
    ]
    deduped, report = aggregate_findings(proposals)
    assert len(deduped) == 2  # f1+f2 合并, f3 独立
    merged = next(d for d in deduped if d.finding_id == "f1")
    # 保留最高 severity (critical from f2)
    assert merged.severity == "critical"
    # 合并 review_dimensions
    assert set(merged.review_dimensions) == {"method-execution-path", "method-red-team"}
    assert set(merged.merged_from) == {"f1", "f2"}
    # 去重报告: f2 并进 f1
    assert report["f1"] == ["f2"]
    assert report["f3"] == []


def test_distinct_root_causes_not_merged() -> None:
    """不同 location 不合并 (即使同 artifact)."""
    proposals = [
        _p("a", "rs", "x.py", "x.py:1", "major", "method-execution-path"),
        _p("b", "rs", "x.py", "x.py:99", "major", "method-execution-path"),
    ]
    deduped, _ = aggregate_findings(proposals)
    assert len(deduped) == 2


def test_order_is_deterministic_first_seen() -> None:
    """输出顺序 = 各根因首次出现顺序 (确定性, 非随机)."""
    proposals = [
        _p("z", "rsZ", "z.py", "z.py:1", "minor", "method-red-team"),
        _p("a", "rsA", "a.py", "a.py:1", "minor", "method-consistency"),
        _p("z2", "rsZ", "z.py", "z.py:1", "major", "method-execution-path"),  # 同 z 根因
    ]
    deduped, _ = aggregate_findings(proposals)
    assert [d.finding_id for d in deduped] == ["z", "a"]  # z 先出现
    assert deduped[0].severity == "major"  # z2 的 major 提升


def test_empty_proposals() -> None:
    deduped, report = aggregate_findings([])
    assert deduped == []
    assert report == {}
