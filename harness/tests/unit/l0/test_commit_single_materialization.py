"""commit-path-single-index-materialization@v1 — 单次提交至多一次全量索引物化 regression suite.

Production pathology (2026-07-03, 性能冲刺): 一次 commit gate attempt_commit 里, gate 在
_run_checks + capsule-derive + abandoned-recovery 路径上调了 5-6 次 `self._event_log.all_records()`,
每次都是逐条 EventRecord.model_validate_json 全量物化。生产账本 (26.99万事件/377MB) 上单次全量
5~8s CPU (main 无 A1-A3 时 ~29s), 5-6 次 → 单提交烧掉数分钟 CPU, 全系统写操作串在全局锁后 →
提交队列小时级堆积。

Fix (commit-path-single-index-materialization@v1, commit e865ff962): 那几处改为复用 EventLog 的
warm committed_index 的 index.records()。

Spy 口径 (2026-07-04 独立 review r3 修正——第一版 spy 挂 EventLog._iter_committed_records 的洞):
adopt 命中的冷进程拿到的是 LazyEventIndex, 其首次 .records() 走 LazyEventIndex._materialize
(index.py:378, 同样逐条 EventRecord.model_validate_json), **不经过** _iter_committed_records ——
旧 spy 对这条路径的账本规模解析完全失明, 测试假绿。现改为直接对 EventRecord.model_validate_json
本身计数 (任何路径的解析都逃不过它), 并补上"已有全量 .idx + 小活动段 delta"的生产常态形状场景。

真实的成本结构 (契约原文="至多一次全量物化", 不是 0 次):
- adopt 命中的冷进程首次取全量记录 = 契约允许的那一次 (LazyEventIndex materialize, 账本规模);
- 同进程内后续 5 处消费复用同一 index 的已物化缓存 ≈ 0;
- pre-fix: 每处各自 all_records() → ~5-6 × 账本规模。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from towow.l0.commit_gate import CommitGate
from towow.l0.event_log import EventLog
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede
from towow.schemas.event_record import EventRecord

if TYPE_CHECKING:
    from pathlib import Path


def _make_envelope_intent(task_id: str) -> EventIntent:
    """A minimal well-formed envelope EventIntent (mirrors test_scan_abandoned_perf範式)."""
    return EventIntent(
        local_intent_id=f"env-{task_id}",
        event_type=EventType.TRANSACTION_ENVELOPE_SUBMITTED,
        event_category=EventCategory.ENVELOPE,
        payload={
            "task_id": task_id,
            "run_id": f"r-{task_id}",
            "capsule_compiled_event_id": "evt-stub-capsule-single-mat",
            "read_set": [{"entity_type": "task", "entity_id": task_id, "version": "evt-cap"}],
            "write_set": [{"entity_type": "task", "entity_id": task_id, "change_type": "modified"}],
            "patches": [],
            "self_check": {"passed": True, "checks_run": ["smoke"]},
            "as_of_projection_seq": 0,
        },
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=f"fork-{task_id}",
            task_id=task_id,
            run_id=f"r-{task_id}",
            parent_session_id=f"sess-{task_id}",
            fork_name=f"fork-{task_id}",
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.TASK, entity_id=task_id, role=SubjectRole.PRIMARY)],
        schema_version="1.0.0",
    )


class _ParseSpy:
    """对 EventRecord.model_validate_json 类方法计数的 spy——任何解析路径都逃不过它.

    (r3 review 修正: 旧 spy 挂 EventLog._iter_committed_records, 对 LazyEventIndex._materialize
    这条同样账本规模的解析路径失明。)
    """

    def __init__(self) -> None:
        self.n = 0
        self._real = EventRecord.model_validate_json

    def __enter__(self) -> _ParseSpy:
        spy = self

        def counting(cls, *args, **kwargs):  # noqa: ANN002, ANN003
            spy.n += 1
            return spy._real(*args, **kwargs)

        EventRecord.model_validate_json = classmethod(counting)  # type: ignore[method-assign, assignment]
        return self

    def __exit__(self, *exc: object) -> None:
        EventRecord.model_validate_json = self._real  # type: ignore[method-assign]


# 单次 commit 自身产生的新记录 + 断言余量。注意 (本测试第一版的错误假设, 实测修正):
# 增量 fold (_fold_active_segment_tail) 的解析成本是 O(active segment) 而非 O(纯 delta)——
# 它 iter 整个活动段再按 seq 过滤 (event_log.py:1309-1313)。生产上活动段有 1万条/10MB 轮转
# 上界, 所以 fold 有界且 ≪ 26.99万全量, 架构健康; 但小账本测试里 segment=整个账本, 一次
# fold ≈ 一轮账本解析。断言按此真实成本模型设计。
_SLACK = 25


def test_warm_commit_parses_at_most_one_segment_pass(tmp_path: Path) -> None:
    """warm 索引下一次 attempt_commit 至多一轮 active-segment 解析 (fold), 无重复全量.

    Pre-fix: 5-6 处 all_records() → 解析 ≥5×账本规模 → 本测试 FAIL。
    Post-fix: 唯一解析来源是 write 后 catch-up 的一次 fold (测试里 segment=全账本 → ≈1 轮)。
    断言 ≤ 1×账本+slack: 将来回归引入任何额外的账本规模解析 (第二处 all_records /
    重复 materialize) → ≥2 轮 → FAIL。
    """
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)

    # Seed: 30 次真实提交 → 账本非空且同进程内索引已 warm。
    for i in range(30):
        assert gate.attempt_commit(_make_envelope_intent(f"seed{i}"), domain_intents=[])[0] is not None
    ledger_n = len(log.all_records())

    with _ParseSpy() as spy:
        measured = gate.attempt_commit(_make_envelope_intent("measured"), domain_intents=[])
    assert measured[0] is not None  # 行为不变: 被测提交仍成功

    # 成本模型 (实测钉死): 一次 attempt_commit 内有两次写落盘 (path-B envelope + path-A batch),
    # 各触发一次 catch-up fold; fold 解析整个 active segment (测试里段=账本) → ≈2×段。
    # 生产: 段有 1万条轮转上界 → fold 有界且 ≪ 账本全量, 架构健康 (T-FND-02 既有行为)。
    assert spy.n <= 2 * ledger_n + _SLACK, (
        f"warm 索引下单次提交应至多 2 轮段级 fold 解析 (≤2×{ledger_n}+{_SLACK}), 实测 {spy.n} —— "
        f"存在额外的账本规模解析 (commit-path-single-index-materialization@v1 回归; pre-fix ≥5×账本)"
    )


def test_cold_adopt_commit_parses_at_most_one_ledger_pass(tmp_path: Path) -> None:
    """生产常态形状 (已有全量 .idx + 冷进程 adopt) 下, 单次提交至多一轮账本规模解析——契约原文.

    r3 review 抓到的洞的直接回归钉: adopt 命中的冷进程首次 .records() 触发 LazyEventIndex
    全量 materialize (≈1×账本规模, 契约允许的那一次); 后续 5 处消费必须复用缓存 (≈0)。
    Pre-fix: 5-6 处各自全量 → ≥5×账本规模 → FAIL。
    """
    seed_log = EventLog(tmp_path / "events.log")
    seed_gate = CommitGate(seed_log)
    for i in range(60):
        assert seed_gate.attempt_commit(_make_envelope_intent(f"seed{i}"), domain_intents=[])[0] is not None
    # seed 阶段每次 commit 内 committed_index() 已把全量 .idx persist 到盘 (生产常态: .idx 存在且新鲜)
    ledger_n = len(seed_log.all_records())
    assert ledger_n > 4 * _SLACK  # 场景前提: 账本规模远大于断言余量

    # 冷进程: 全新 EventLog 实例 adopt 持久化 .idx (真实 CLI 每命令新进程语义)。
    cold_log = EventLog(tmp_path / "events.log")
    cold_gate = CommitGate(cold_log)

    with _ParseSpy() as spy:
        measured = cold_gate.attempt_commit(_make_envelope_intent("cold-measured"), domain_intents=[])
    assert measured[0] is not None

    # 至多: 1 轮全量 materialize (契约允许的那一次, LazyEventIndex 首次 .records()) +
    # 2 轮段级 fold (两次写落盘各一次 catch-up; 测试里段=账本)。pre-fix: 5-6 轮全量 + fold。
    budget = 3 * ledger_n + _SLACK
    assert spy.n <= budget, (
        f"冷 adopt 进程单次提交应至多 1 全量 materialize + 2 段 fold (≤{budget}), 实测 {spy.n} —— "
        f"多处调用点各自做全量解析 (契约'至多一次全量物化'被破坏; pre-fix ≥5 轮全量)"
    )
    # 反向卫兵: 若解析数连一轮的一半都不到, 说明场景没搭出"adopt→materialize"的真实形状
    # (比如 .idx 没被 adopt、或 records() 没被真正调用), 测试自身失效——宁可红了来查。
    assert spy.n >= ledger_n // 2, (
        f"场景自检失败: 解析数 {spy.n} 远小于账本规模 {ledger_n}, 本测试没有踩到 "
        f"adopt→首次 materialize 的目标路径, 需检查场景搭建"
    )


def test_commit_behavior_unchanged_reads_same_committed_snapshot(tmp_path: Path) -> None:
    """数据等价性行为基线: 复用 index.records() 后, 提交的接受/落账语义与逐次 all_records() 一致。

    This is the behavioral ruler the perf change must not move — a straightforward series of commits
    still all accept and land, proving index.records() feeds the checks the same committed snapshot
    all_records() did. Passes pre- and post-fix (it is the invariance baseline, not the perf ruler).
    """
    log = EventLog(tmp_path / "events.log")
    gate = CommitGate(log)

    envelopes = [gate.attempt_commit(_make_envelope_intent(f"t{i}"), domain_intents=[])[0] for i in range(6)]
    assert all(e is not None for e in envelopes)

    # 每次提交都写了 envelope + CommitAccepted sentinel → committed 流里两类事件数一致。
    accepted = log.get_events_by_type(EventType.COMMIT_ACCEPTED)
    submitted = log.get_events_by_type(EventType.TRANSACTION_ENVELOPE_SUBMITTED)
    assert len(accepted) == 6, f"expected 6 accepted commits, got {len(accepted)}"
    assert len(submitted) == 6, f"expected 6 submitted envelopes, got {len(submitted)}"
