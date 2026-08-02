"""M-0.2 ProjectionStore + 10 reducers (4 detailed + 6 stub).

# spec source: 03-l0-truth-source/M-0.2-projection-detailed-design.md
#   §2.3 Lazy Catch-Up update mode
#   §3.1-3.6 6 detailed projection schemas + reducers
#   §3.10-3.11 finding_lifecycle / detection_rule_lifecycle
#   §5.1-5.3 batch-level update + cursor advance
# Patch M-2.3-E: escalation_lifecycle projection (M-2.3 §6.7) — reducer 实装 RUN-038 L0增强
# Patch M-0.6-E: obligation_lifecycle.canonical_state field
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

# K2b-REG (50-graph-protocol §TG.4): 图协议三类新节点的纯 reducer (leaf 模块, 只依赖 typing — 无环)。
# 接入策略 = 全量 re-fold (recompute-style, 同 impact_graph): 不是 per-event delta merge。
# reduce_node_events 是 whole-history fold (LockReleased 失活的 held-by 边必须与 LockAcquired 在同
# 一次 fold 里, 单事件折叠会静默 no-op), 故每次从全量 node 事件重折、覆盖三文件。这也让 node_reducers
# 在生产里真正 load-bearing (不在 projection.py 重写逻辑 = 不让那 50 测试的纯函数 tested-but-dead)。
from towow.awareness.node_reducers import reduce_node_events
from towow.schemas.enums import (
    DebtType,
    EscalationLifecycleState,
    EventCategory,
    EventType,
    ObligationCanonicalState,
    ObligationFieldsLifecycleState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
)
from towow.schemas.projection_state import (
    EscalationLifecycleEntry,
    ObligationLifecycleNode,
    ObligationLifecycleStateProjection,
)

if TYPE_CHECKING:
    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord


# M-0.2 §3.7 方向归一化 — impact_graph reducer 翻 source/target 的概念 edge_type。
# dependency/reference 概念边原始约定 source=下游, impact_graph 归一成 source=上游 (对齐任务边
# + O-11 forward 语义)，故翻这两类；contract/containment/data_schema 原样拷 (见 _recompute_impact_graph
# docstring)。提到模块级是为了让 cross_projection_consistency 的一致性 checker 镜像同一归一化规则
# (单一真源；checker 不重复定义 → 不会跟 reducer 漂移导致假阳性)。
IMPACT_GRAPH_NORMALIZE_FLIP_CONCEPT_EDGE_TYPES: tuple[str, ...] = ("dependency", "reference")


class ConsistencyMode(StrEnum):
    """M-0.2a 附录B Patch 2 — Read API 一致性模式。

    fresh_required:     必须 catch up 到最新 committed; 超时 → fail-closed (不返 stale)。
    allow_stale:        接受当前 watermark 的 state, 不触发 catch up。
    watermark_at_least: 必须 catch up 到至少 min_watermark; 未达且超时 → fail-closed。
    """

    FRESH_REQUIRED = "fresh_required"
    ALLOW_STALE = "allow_stale"
    WATERMARK_AT_LEAST = "watermark_at_least"


class ProjectionConsistencyError(RuntimeError):
    """M-0.2a Patch 2 fail-closed 信号 — 要求的一致性未达 (绝不静默返陈旧态)。"""


_PROJECTION_FILES = {
    "concept_graph": "concept_graph.json",
    "task_graph": "task_graph.json",
    "obligation_lifecycle_state": "obligation_lifecycle_state.json",
    "ownership": "ownership.json",
    "at_reference_graph": "at_reference_graph.json",
    "consumer_relation": "consumer_relation.json",
    "finding_lifecycle": "finding_lifecycle.json",
    "detection_rule_lifecycle": "detection_rule_lifecycle.json",
    "escalation_lifecycle": "escalation_lifecycle.json",  # Patch M-2.3-E
    "commit_history": "commit_history.json",
    # T-FU-09 可观测 — commit gate 每道 check 的触发计数 (accepted 路径 checks_passed 的 result 分布 +
    # rejected 路径 failure_reason 分布)。承接 live-target-execution-evidence@v1 goal 收口门的运行观测:
    # 能区分"门在跑但从没真评估过 observable (潜伏, result=not_applicable)" vs "真评估 N 次/拒 M 次"。
    # commit_history 只存 verdict 不存 checks_passed 明细, 故不能复用它 — 这是从账本干净重建的专用小投影。
    "gate_check_firing": "gate_check_firing.json",
    "debt_registry": "debt_registry.json",  # RUN-029 第0波 ③ — self-debt honest ledger
    "capability_status": "capability_status.json",  # T-TRACK-01 — event-sourced 603 board
    # T-L1-06 (RUN-032) — M-1.1 §3.1/§10.1/§13.2 三个采访侧 projection
    "information_need_graph": "information_need_graph.json",
    "interview_progress": "interview_progress.json",
    "interview_brief_index": "interview_brief_index.json",
    # RUN-035 0-E-4 — M-1.5 评审侧 projection (T-L1-50 review_plan)
    "review_plan": "review_plan.json",
    # T-L1-15 (波3) — M-1.2 §10.2 Patch T 第 3 个 reducer: per-plan 批次冻结状态
    "engineering_consensus_index": "engineering_consensus_index.json",
    # RUN-038 L0增强 — M-0.2 §3.7/§7.6 二阶 projection: 从 concept_graph + task_graph 派生
    "impact_graph": "impact_graph.json",
    # RUN-038 L0增强 — M-0.2 附录B Patch 6 decision projection (具体 semantic_judgment event +
    # supersede 链推导 current_validity)
    "decision": "decision.json",
    # K2b-REG (50-graph-protocol §TG.4) — 图协议三类新节点的统一-shape 投影 (facade._load_native 直接读)。
    # 由 reduce_node_events 全量 re-fold 产出 (recompute-style)。当前消费 SessionSpawned/LockAcquired:
    # session_graph 真物化血缘 (依赖 part 3 spawn 收口 emit); lock_graph 只 acquire 侧 (release 缺口交回);
    # escalation_graph 是空占位 — 真兑现须接 World-B + solution-4 (见 _reduce_node_graphs 注 / K2b-REG 交回)。
    "session_graph": "session_graph.json",
    "lock_graph": "lock_graph.json",
    "escalation_graph": "escalation_graph.json",
}

# M-0.2 §4.1.2 get_neighborhood — graph projections it can BFS over, and the field names of
# their node-id / edge-endpoint keys. concept_graph + task_graph are the two §4.1.2 "概念图 / 任务图"
# graph projections; other projections are not node/edge shaped so get_neighborhood rejects them.
_NEIGHBORHOOD_SCHEMA: dict[str, tuple[str, str, str]] = {
    "concept_graph": ("concept_id", "source_concept_id", "target_concept_id"),
    "task_graph": ("task_id", "source_task_id", "target_task_id"),
}


class ProjectionStore:
    """File-backed projection store with atomic writes (os.replace + fsync).

    Each projection lives at .towow/graph/<name>.json. Catchup is lazy — invoked on
    read / submit boundary (M-0.2 §2.3).
    """

    def __init__(self, graph_dir: Path) -> None:
        self._dir = graph_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cursor_path = self._dir / ".cursor"
        if not self._cursor_path.exists():
            self._cursor_path.write_text("0", encoding="utf-8")
        # Set during catchup so reducers that need a cross-event lookup (ownership release on
        # CommitAccepted → resolve envelope_event_id → envelope.write_set) can reach the log.
        self._reduce_event_log: EventLog | None = None
        # 批次内读写合并 (perf) — catchup() 单次批内, 同一投影被多条事件反复读/写时, 不逐次落盘。
        # None = 无活跃批次 (read/write_atomic 走原始逐次落盘路径, 与 restore_bundle /
        # recompute_one / rebuild 的独立读写完全一致, 不受此机制影响)。非 None 时 = 批内内存态
        # (投影名 → 当前内存内容, 可能是 None 表示磁盘上尚不存在); 只在 catchup() 内部
        # try/finally 打开与归零, 绝不跨调用泄漏 (每次 catchup() 都是全新一批)。
        self._batch_cache: dict[str, dict[str, object] | None] | None = None
        # 本批次内真正被 write_atomic 过的投影名集合 —— 只有它们在批尾落盘一次, 也只有它们的
        # meta 需要在本批后重新持久化 (见 _persist_all_meta 收窄)。
        self._batch_dirty: set[str] = set()

    @property
    def cursor(self) -> int:
        return int(self._cursor_path.read_text(encoding="utf-8").strip())

    def _advance_cursor(self, seq: int) -> None:
        tmp = self._cursor_path.with_suffix(".tmp")
        tmp.write_text(str(seq), encoding="utf-8")
        tmp.replace(self._cursor_path)

    def write_atomic(self, name: str, data: dict[str, object]) -> None:
        """Atomic write per M-0.2 §5.3 — write to .tmp then os.replace + fsync.

        批次内读写合并 (perf): when a catchup() batch is open (``self._batch_cache is not
        None``), this only updates the in-memory batch state (and marks ``name`` dirty) — the
        real disk write happens once at batch-flush time via ``_write_disk`` (see catchup()).
        Outside a batch (the common per-call path used by restore_bundle / recompute_one /
        rebuild's own reducer replay) this writes straight through, unchanged from before.
        """
        if name not in _PROJECTION_FILES:
            raise ValueError(f"unknown projection: {name}")
        if self._batch_cache is not None:
            self._batch_cache[name] = data
            self._batch_dirty.add(name)
            return
        self._write_disk(name, data)

    def _write_disk(self, name: str, data: dict[str, object]) -> None:
        """The actual atomic tmp→fsync→rename write (M-0.2 §5.3) — unchanged semantics,
        factored out so both the direct path and the batch-flush path share it."""
        path = self._dir / _PROJECTION_FILES[name]
        tmp = path.with_suffix(".tmp")
        # 中文落字面 (非 ascii 转义): 投影是 events.log 的派生 grep 面 (concept_graph.json 等)。
        # json 默认会把中文概念名转义成 \uXXXX → 任何按中文名 grep 该文件的 agent/工具/owner 得
        # 假阴性 (节点存在却 0 命中)。events.log 本身已存字面中文; 此处对齐, 让派生图也可被 grep。
        # state_hash 是每次 persist 刷新的描述性 hash, 非历史链, 改序列化随之自然刷新。
        # finding-projection-cjk-ascii-escape-grep-false-negative。
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        os_replace_with_fsync(tmp, path, self._dir)

    def read(self, name: str) -> dict[str, object] | None:
        """批次内读写合并 (perf): when a catchup() batch is open, the projection's first read()
        in this batch loads from disk once and caches it; every subsequent read()/write_atomic()
        for the same name in the same batch hits the in-memory cache only — including reads that
        happen after another reducer already write_atomic'd a fresher value this batch (e.g.
        _recompute_impact_graph reading the just-updated concept_graph/task_graph), since both
        read() and write_atomic() share the same ``self._batch_cache`` dict. Outside a batch this
        is the original disk-read-every-call behavior."""
        if name not in _PROJECTION_FILES:
            raise ValueError(f"unknown projection: {name}")
        if self._batch_cache is not None:
            if name not in self._batch_cache:
                self._batch_cache[name] = self._read_disk(name)
            return self._batch_cache[name]
        return self._read_disk(name)

    def _read_disk(self, name: str) -> dict[str, object] | None:
        path = self._dir / _PROJECTION_FILES[name]
        if not path.exists():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None

    def findings_for_review_unit(self, review_unit_id: str) -> list[dict[str, object]]:
        """T-LND-01 (INV-B1-1 / review-unit@v1): 返回归属某 review-unit 的全部 finding 当前状态.

        derived-review-verdict-projection 折叠 verdict 的输入 —— 一个 review-unit 的全部 finding.
        投影缺失 (从未归约过 finding) → 空列表; review_unit_id 无匹配 → 空列表 (不抛)。
        review_unit_id 空 (None/"") 不是合法 review-unit → 空列表 (否则把系统/execution 侧 None
        键 finding 一锅端误当同一 review-unit)。
        """
        if not review_unit_id:
            return []
        existing = self.read("finding_lifecycle") or {}
        findings = existing.get("findings", [])
        if not isinstance(findings, list):
            return []
        return [f for f in findings if isinstance(f, dict) and f.get("review_unit_id") == review_unit_id]

    def catchup(self, event_log: EventLog) -> int:
        """M-0.2 §2.3 Lazy Catch-Up — replay events from cursor → current max_seq.

        Reducers run idempotently — full rebuild from event log produces identical state.
        Returns number of events processed.

        批次内读写合并 (perf): a single batch may touch the same projection many times (e.g.
        concept_graph + task_graph both feeding _recompute_impact_graph on every event) — without
        coalescing, each touch was an independent disk read-modify-write (json.loads + tmp-write +
        fsync + os.replace), the dominant cost of a large catch-up. Opens ``self._batch_cache`` for
        the duration of this call: every ``read()``/``write_atomic()`` a reducer makes during the
        event loop hits the in-memory cache (see read()/write_atomic()); only the projections
        actually written this batch (``self._batch_dirty``) are flushed to disk, each exactly once,
        via the SAME atomic ``_write_disk`` path (tmp→fsync→rename semantics unchanged — no
        durability weakening). The flush happens BEFORE the cursor advances and BEFORE meta is
        persisted, preserving the pre-batching crash-recovery invariant: if the process dies
        mid-flush, the cursor has NOT moved yet, so the next catchup() safely replays the identical
        idempotent batch from the same old position (reducers are upsert-shaped, so a partial
        flush followed by a full re-apply reproduces the same final state — exactly the guarantee
        the original per-event synchronous write already relied on).
        """
        cursor_value = self.cursor
        events = event_log.get_events_in_range(cursor_value, event_log.next_sequence - 1)
        if not events:
            return 0
        events.sort(key=lambda r: r.sequence_number)
        self._reduce_event_log = event_log
        self._batch_cache = {}
        self._batch_dirty = set()
        try:
            for ev in events:
                self._apply(ev)
            for name in self._batch_dirty:
                dirty_data = self._batch_cache[name]
                # write_atomic() only ever stores a real dict in the cache for a name it adds to
                # _batch_dirty (never None — None only occurs for a name whose first read() found
                # no on-disk file yet); this assert documents/enforces that invariant for mypy.
                assert dirty_data is not None, f"batch-dirty projection {name!r} has no cached data"
                self._write_disk(name, dirty_data)
            touched = self._batch_dirty
        finally:
            self._batch_cache = None
            self._batch_dirty = set()
        new_cursor = events[-1].sequence_number + 1
        self._advance_cursor(new_cursor)
        # M-0.2 附录B Patch 3 — after catch_up_all advances the cursor, persist each materialized
        # projection's meta (watermark + state_hash + generation) so a crash mid-write is detectable
        # and recover_protocol has an authoritative recovery position. watermark = last consumed seq.
        # Narrowed to `touched` (perf, 顺手): only the projections this batch actually wrote need
        # their state re-hashed / meta re-persisted — a projection untouched by this batch already
        # has a valid meta pair from whenever it was last written (recover()'s per-projection
        # hash-consistency check is unaffected: it only ever compares a projection's OWN current
        # state against its OWN last-persisted meta, never against the global cursor).
        self._persist_all_meta(new_cursor - 1, touched)
        return len(events)

    def read_consistent(
        self,
        event_log: EventLog,
        *,
        mode: ConsistencyMode,
        min_watermark: int | None = None,
        timeout_ms: int = 2000,
    ) -> int:
        """M-0.2a Patch 2 — catch up under a consistency mode; return resulting watermark (cursor-1).

        T-L0-12 (波1): 给多消费方差异化一致性需求 (M-0.3/M-0.5 fresh_required; M-0.7 snapshot
        watermark_at_least(cutoff_seq); CLI dashboard allow_stale)。

        - allow_stale:        不触发 catch up, 直接返回当前 watermark (可能陈旧)。
        - fresh_required:     catch up 到最新 committed 后返回 (单进程 catchup 同步完成即最新)。
        - watermark_at_least: catch up; 达到 min_watermark 即返回; 未达则轮询至 timeout_ms 后
          **fail-closed** (抛 ProjectionConsistencyError, 绝不静默返陈旧态)。timeout_ms=0 → 一次
          尝试不达即刻 fail-closed。

        fail-closed 是合约: 未达水位时宁可报错也不返回缺 event 的 projection (审计/snapshot 不可接受
        陈旧态导致漏判)。
        """
        if mode is ConsistencyMode.ALLOW_STALE:
            return max(0, self.cursor - 1)  # 不 catch up — 接受当前(可能陈旧)水位
        if mode is ConsistencyMode.WATERMARK_AT_LEAST and min_watermark is None:
            msg = "watermark_at_least 模式必须给 min_watermark"
            raise ValueError(msg)

        deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
        while True:
            self.catchup(event_log)
            watermark = max(0, self.cursor - 1)
            if mode is ConsistencyMode.FRESH_REQUIRED:
                return watermark  # catchup 已到最新 committed-visible — 即 fresh
            if min_watermark is not None and watermark >= min_watermark:
                return watermark
            if time.monotonic() >= deadline:
                msg = (
                    f"consistency {mode.value} 未达: watermark {watermark} < 要求 {min_watermark} "
                    f"(超时 {timeout_ms}ms, fail-closed — 不返陈旧态)"
                )
                raise ProjectionConsistencyError(msg)
            time.sleep(0.02)  # 短轮询: 等其他进程的写入推进 committed 流

    def _apply(self, ev: EventRecord) -> None:
        """Dispatch to per-event-type reducer (M-0.2 §3)."""
        if ev.event_type in _OBLIGATION_LIFECYCLE_EVENTS:
            self._reduce_obligation_lifecycle(ev)
        elif ev.event_type is EventType.COMMIT_ACCEPTED:
            self._reduce_commit_history(ev, verdict="accepted")
        elif ev.event_type is EventType.COMMIT_REJECTED:
            self._reduce_commit_history(ev, verdict="rejected")
        elif ev.event_type in _CONCEPT_GRAPH_EVENTS:
            self._reduce_concept_graph(ev)
        elif ev.event_type in {EventType.DEBT_REGISTERED, EventType.DEBT_RESOLVED}:
            self._reduce_debt_registry(ev)
        elif ev.event_type in {
            EventType.CAPABILITY_BASELINE_INGESTED,
            EventType.CAPABILITY_STATUS_ADVANCED,
        }:
            self._reduce_capability_status(ev)  # T-TRACK-01
        elif ev.event_type in _TASK_GRAPH_EVENTS:
            self._reduce_task_graph(ev)  # T-L0-06
        elif ev.event_type in _AT_REFERENCE_EVENTS:
            self._reduce_at_reference_graph(ev)  # T-L0-08
        # ownership (T-L0-07) is fed by events that also feed other projections
        # (TaskWriteSetClaimed → task_graph; CommitAccepted → commit_history), so it runs
        # additively after the elif chain rather than as an exclusive branch.
        if ev.event_type in _OWNERSHIP_EVENTS:
            self._reduce_ownership(ev)
        # T-FU-09 可观测 — gate_check_firing 与 commit_history 同源 (CommitAccepted/Rejected) 但读的是
        # checks_passed/failure_reason 明细, 故 additive 分支 (commit_history 在上面 elif 链已消费同事件)。
        if ev.event_type is EventType.COMMIT_ACCEPTED:
            self._reduce_gate_check_firing(ev, verdict="accepted")
        elif ev.event_type is EventType.COMMIT_REJECTED:
            self._reduce_gate_check_firing(ev, verdict="rejected")
        # T-L1-06 (RUN-032) — 采访侧 3 projection. InformationNeed* / InterviewBriefPublished
        # 也喂 concept_graph (brief seed ConceptCreated 是另一 event_type), 故 additive 分支.
        if ev.event_type in _INFORMATION_NEED_EVENTS:
            self._reduce_information_need_graph(ev)
        if ev.event_type in _INFORMATION_NEED_EVENTS or ev.event_type is EventType.INTERVIEW_BRIEF_PUBLISHED:
            # interview_progress 从 (已更新的) information_need_graph 重算聚合 + brief 标志 — 幂等.
            self._reduce_interview_progress(ev)
        if ev.event_type is EventType.INTERVIEW_BRIEF_PUBLISHED:
            self._reduce_interview_brief_index(ev)
        # RUN-035 0-E-4 — review 侧 projection (additive: finding/review_plan 事件不喂别的图)
        if ev.event_type in _FINDING_LIFECYCLE_EVENTS:
            self._reduce_finding_lifecycle(ev)  # T-L1-52/53 三态 (与 T-L1-64 重叠)
        if ev.event_type is EventType.REVIEW_PLAN_CREATED:
            self._reduce_review_plan(ev)  # T-L1-50
        if ev.event_type is EventType.CONSUMER_LIST_PUBLISHED:
            self._reduce_consumer_relation(ev)  # T-L0-09 (波1)
        if ev.event_type is EventType.ENGINEERING_CONSENSUS_FREEZED:
            self._reduce_engineering_consensus_index(ev)  # T-L1-15 (波3) — Patch T 第 3 reducer
        if ev.event_type in _DETECTION_RULE_EVENTS:
            self._reduce_detection_rule(ev)  # T-L0-11 (波1)
        if ev.event_type in _ESCALATION_LIFECYCLE_EVENTS:
            self._reduce_escalation_lifecycle(ev)  # RUN-038 L0增强 (M-2.3 §6.7)
        # M23-F2 / rc-unify-escalation-event-source-materialize-projection: World-B 升级源
        # (GoalEscalationRaised + owner NJ answer) 收进同一 escalation_lifecycle 投影 (World-A 同
        # schema, 向后兼容)。此前 GoalEscalationRaised 走 stub-rewrap NodeTouched 只被 orchestrator
        # 内存态 (pending_escalations) 读、从不进投影 = 久卡 owner 决策落不进 lifecycle (rc-two-
        # escalation-worlds-no-inbox-materialized 的活样本)。additive: World-B 源与 native ESCALATION_*
        # 是不相交事件集, 不重复处理。
        if _world_b_escalation_event(ev)[0] is not None:
            self._reduce_escalation_lifecycle(ev)
        # RUN-038 L0增强 — decision projection (M-0.2 Patch 6): 按 event_category 门收所有
        # semantic_judgment 类 event (ResolverDecisionMade / ObligationScopeJudged / 等), 不枚举
        # 具体 type (Patch 6: 原文引用的统一 SemanticJudgmentMade 不存在, 改 category 门)。additive。
        if ev.event_category is EventCategory.SEMANTIC_JUDGMENT:
            self._reduce_decision(ev)
        # RUN-038 L0增强 — impact_graph 是二阶 projection (M-0.2 §3.7/§7.6): 不直接消费 event,
        # 每次 concept_graph 或 task_graph 更新后从其已物化边集重算。故放 _apply 末尾, 在
        # concept_graph/task_graph reducer (上面 elif 链) 已写盘之后触发。
        if ev.event_type in _CONCEPT_GRAPH_EVENTS or ev.event_type in _TASK_GRAPH_EVENTS:
            self._recompute_impact_graph()
        # K2b-REG (§TG.4) — session/lock/escalation 三图二阶 re-fold: 任一 node 事件 → 全量重折三图。
        # additive (与上面分支不互斥; LockReleased/Escalation* 也喂别的图)。放末尾, 用全量集重折 (见
        # _reduce_node_graphs: whole-history fold, 非 per-event delta)。
        if ev.event_type in _NODE_GRAPH_EVENTS:
            self._reduce_node_graphs()

    def rebuild(self, event_log: EventLog) -> int:
        """M-0.2 §6 full rebuild — discard materialized projection files, reset the
        global cursor to 0, replay the entire event log.

        Reducers are idempotent, so a full rebuild reproduces identical state. Needed
        when a reducer is added after the global cursor has already advanced past the
        historical events it consumes (F-019-8: concept_graph reducer was a stub, so
        every ConceptCreated before this fix is past the cursor and a plain catchup
        cannot backfill it).
        """
        for fname in _PROJECTION_FILES.values():
            (self._dir / fname).unlink(missing_ok=True)
        self._advance_cursor(0)
        return self.catchup(event_log)

    def recompute_one(self, name: str, event_log: EventLog) -> int:
        """Surgically rebuild a single projection from the event log, leaving every other
        projection file AND the global ``.cursor`` byte-for-byte untouched.

        Why this exists (RUN-095 实证): a persisted projection can diverge from what the
        events imply (e.g. a duplicated-seq double-count, or a snapshot-restore truncation),
        yet ``catchup()`` cannot fix it once the global cursor has already passed the events
        (0 events left to replay), and ``rebuild()`` is a one-shot全量 reset that — empirically
        — can REGRESS a healthy projection (commit_history 反退) because the global cursor and
        sibling projections are all torn down together. ``recompute_one`` is the外科式 fix:
        only ``<name>.json`` is discarded and rebuilt; the global cursor and the other 18
        projection files are never read-for-write nor written.

        Correctness contract — the result equals the ``<name>`` slice of a canonical ``rebuild``:
          1. unlink ONLY ``<name>.json`` (sibling projection files + ``.cursor`` untouched).
          2. replay over a LOCAL temp cursor 0 → head via ``event_log.get_events_in_range`` and
             the SAME seq-dedup path ``rebuild``/``catchup`` use (events are taken from the
             committed sequence index, sorted by ``sequence_number``, one record per seq — so a
             polluted source like commit_history's seq-collision double-count is NOT re-counted;
             the result is the canonical 825, not the persisted 885).
          3. apply ONLY the reducer branch(es) ``_apply`` would route to ``<name>`` — no other
             projection's reducer runs, so no neighbor file is mutated.
          4. ``write_atomic(name, …)`` (done inside the reducer) + ``_persist_meta(name, head)``
             so the projection's own meta (watermark/state_hash/generation) advances.
          5. ``self.cursor`` / ``.cursor`` is NEVER modified — the global consumer position is
             orthogonal to a single-projection repair.

        Cross-projection READS are allowed (and required) but are read-only on the neighbor:
        impact_graph (二阶) derives from the already-materialized concept_graph + task_graph,
        and interview_progress recomputes its aggregate from the already-materialized
        information_need_graph. Those neighbors are read at their canonical on-disk state and
        are never written by this call.

        Returns the number of events replayed (== the count ``rebuild`` would replay).
        """
        if name not in _PROJECTION_FILES:
            msg = f"unknown projection: {name}"
            raise ValueError(msg)
        # (1) discard ONLY this projection's materialized state — neighbors + .cursor untouched.
        (self._dir / _PROJECTION_FILES[name]).unlink(missing_ok=True)
        # (2) replay 0 → head over a LOCAL cursor (never self.cursor), same seq-dedup path as
        # rebuild/catchup: get_events_in_range pulls one record per committed seq from the
        # sequence index, then we sort by sequence_number. No double-counting of a polluted log.
        head = event_log.next_sequence - 1
        events = event_log.get_events_in_range(0, head)
        events.sort(key=lambda r: r.sequence_number)
        # _reduce_event_log lets the ownership reducer resolve CommitAccepted → envelope.write_set
        # (same cross-event lookup catchup wires up). Set it for the duration of this replay only.
        prev_reduce_log = self._reduce_event_log
        self._reduce_event_log = event_log
        try:
            for ev in events:
                self._apply_one(name, ev)
        finally:
            self._reduce_event_log = prev_reduce_log
        # (4) advance ONLY this projection's meta to head (watermark = last consumed seq). The
        # projection file itself was written by its reducer via write_atomic; persist its meta so
        # get_watermark/recover see the repaired generation. .cursor is left exactly as it was.
        self._persist_meta(name, head)
        return len(events)

    def _apply_one(self, name: str, ev: EventRecord) -> None:
        """Apply ``ev`` to a SINGLE projection ``name`` only — the per-projection mirror of
        ``_apply``'s dispatch.

        Each branch is the exact (event_type / event_category) gate ``_apply`` uses to route
        an event to that projection's reducer, lifted out so recompute_one can run one
        projection's reducer in isolation without firing any other projection's reducer. This
        is deliberately an explicit per-name table (not a generic registry) so it stays
        auditable against ``_apply``: the gate for ``name`` here must match the gate for the
        same reducer there.
        """
        et = ev.event_type
        if name == "obligation_lifecycle_state":
            if et in _OBLIGATION_LIFECYCLE_EVENTS:
                self._reduce_obligation_lifecycle(ev)
        elif name == "commit_history":
            if et is EventType.COMMIT_ACCEPTED:
                self._reduce_commit_history(ev, verdict="accepted")
            elif et is EventType.COMMIT_REJECTED:
                self._reduce_commit_history(ev, verdict="rejected")
        elif name == "gate_check_firing":
            if et is EventType.COMMIT_ACCEPTED:
                self._reduce_gate_check_firing(ev, verdict="accepted")
            elif et is EventType.COMMIT_REJECTED:
                self._reduce_gate_check_firing(ev, verdict="rejected")
        elif name == "concept_graph":
            if et in _CONCEPT_GRAPH_EVENTS:
                self._reduce_concept_graph(ev)
        elif name == "debt_registry":
            if et in {EventType.DEBT_REGISTERED, EventType.DEBT_RESOLVED}:
                self._reduce_debt_registry(ev)
        elif name == "capability_status":
            if et in {EventType.CAPABILITY_BASELINE_INGESTED, EventType.CAPABILITY_STATUS_ADVANCED}:
                self._reduce_capability_status(ev)
        elif name == "task_graph":
            if et in _TASK_GRAPH_EVENTS:
                self._reduce_task_graph(ev)
        elif name == "at_reference_graph":
            if et in _AT_REFERENCE_EVENTS:
                self._reduce_at_reference_graph(ev)
        elif name == "ownership":
            if et in _OWNERSHIP_EVENTS:
                self._reduce_ownership(ev)
        elif name == "information_need_graph":
            if et in _INFORMATION_NEED_EVENTS:
                self._reduce_information_need_graph(ev)
        elif name == "interview_progress":
            # _apply gate: any information-need event OR a brief publish. The reducer recomputes
            # its aggregate from the already-materialized information_need_graph neighbor (read
            # only) — so a faithful single-projection recompute requires that neighbor be current.
            if et in _INFORMATION_NEED_EVENTS or et is EventType.INTERVIEW_BRIEF_PUBLISHED:
                self._reduce_interview_progress(ev)
        elif name == "interview_brief_index":
            if et is EventType.INTERVIEW_BRIEF_PUBLISHED:
                self._reduce_interview_brief_index(ev)
        elif name == "finding_lifecycle":
            if et in _FINDING_LIFECYCLE_EVENTS:
                self._reduce_finding_lifecycle(ev)
        elif name == "review_plan":
            if et is EventType.REVIEW_PLAN_CREATED:
                self._reduce_review_plan(ev)
        elif name == "consumer_relation":
            if et is EventType.CONSUMER_LIST_PUBLISHED:
                self._reduce_consumer_relation(ev)
        elif name == "engineering_consensus_index":
            if et is EventType.ENGINEERING_CONSENSUS_FREEZED:
                self._reduce_engineering_consensus_index(ev)
        elif name == "detection_rule_lifecycle":
            if et in _DETECTION_RULE_EVENTS:
                self._reduce_detection_rule(ev)
        elif name == "escalation_lifecycle":
            # native World-A 升级事件 + World-B 源 (GoalEscalationRaised / owner NJ) 同喂一个 reducer
            # (M23-F2 统一)，recompute_one 据此回放两族, 与 _apply 的双分支门一致。
            if et in _ESCALATION_LIFECYCLE_EVENTS or _world_b_escalation_event(ev)[0] is not None:
                self._reduce_escalation_lifecycle(ev)
        elif name == "decision":
            if ev.event_category is EventCategory.SEMANTIC_JUDGMENT:
                self._reduce_decision(ev)
        elif name == "impact_graph":
            # 二阶 projection: _apply recomputes it after any concept/task event from the
            # already-materialized concept_graph + task_graph (read-only on those neighbors).
            # Recomputing once per such event reproduces the same final state idempotently, but
            # since it derives purely from the (unchanged-by-us) neighbor files, doing it once is
            # sufficient — gate on the same events _apply uses so empty logs leave it untouched.
            if et in _CONCEPT_GRAPH_EVENTS or et in _TASK_GRAPH_EVENTS:
                self._recompute_impact_graph()
        elif name in ("session_graph", "lock_graph", "escalation_graph"):
            # K2b-REG §TG.4: 三图同源全量 re-fold (recompute-style)。only=name → 只落自己那份,
            # 不碰邻居 (满足 recompute_one 合约)。门同 _apply: 仅 node 事件触发 (空日志不写)。
            if et in _NODE_GRAPH_EVENTS:
                self._reduce_node_graphs(only=name)
        else:  # pragma: no cover - guarded by recompute_one's _PROJECTION_FILES membership check
            msg = f"no single-projection reducer route for {name!r}"
            raise ValueError(msg)

    def restore_bundle(self, bundle: dict[str, str], *, cutoff_seq: int) -> list[str]:
        """M-0.2 §5.4 restore step 1-2 — load snapshot projection state + set watermark.

        Inverse-load half of snapshot restore (the snapshot bundle file I/O + integrity live in
        M-0.7 restore_from_snapshot; M-0.2 owns its own cursor). ``bundle`` maps projection_name →
        JSON text (the per-projection bundle files M-0.7 dumped at snapshot time). Each known
        projection is written atomically (replacing current state), then the cursor is set to
        ``cutoff_seq + 1`` — events through cutoff_seq are already reflected in the bundle, so the
        next unprocessed sequence is cutoff_seq + 1. The caller then catchup()s to replay events
        > cutoff_seq (§5.4 step 3-5).

        Unknown projection names are skipped (forward-compat: a snapshot may carry a projection
        this build does not register). A projection absent from the bundle is left untouched — it
        had no pre-cutoff state, so catchup over post-cutoff events reconstructs it (idempotent).
        Returns the sorted names actually restored.
        """
        restored: list[str] = []
        for name in sorted(bundle):
            if name not in _PROJECTION_FILES:
                continue
            try:
                data = json.loads(bundle[name])
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            self.write_atomic(name, data)
            restored.append(name)
        self._advance_cursor(cutoff_seq + 1)
        return restored

    def get_projection_bundle(
        self,
        names: list[str],
    ) -> dict[str, dict[str, object] | None]:
        """M-0.2 §4 multi-projection atomic read (Patch 5)."""
        return {name: self.read(name) for name in names}

    # ─── Read API (M-0.2 §4.1) ───────────────────────────────────────────────
    # The 6 §4.1 query functions. Catch-up policy follows §4.2: the functions that return projection
    # state (get_projection / get_neighborhood / get_impact_slice) trigger catch-up first
    # (read-triggers-catch-up — the consumer never reasons about freshness); get_watermark does NOT
    # (§4.1.5 explicitly "不触发 catch up"); get_entity_history / get_supersede_chain read the M-0.1
    # event log directly (subjects[] / supersede.idx), independent of projection materialization.

    def get_projection(
        self,
        event_log: EventLog,
        projection_type: str,
    ) -> dict[str, object]:
        """M-0.2 §4.1.1 — full current projection state + watermark.

        Read-triggers-catch-up (§4.2): catches up to the latest committed-visible state before
        returning, so the caller always sees fresh state without judging freshness itself. Returns
        {"state": ProjectionState, "watermark": uint64}.
        """
        if projection_type not in _PROJECTION_FILES:
            msg = f"unknown projection: {projection_type}"
            raise ValueError(msg)
        self.catchup(event_log)  # §4.2 read-triggers-catch-up
        state = self.read(projection_type) or {}
        return {"state": state, "watermark": max(0, self.cursor - 1)}

    def get_neighborhood(
        self,
        event_log: EventLog,
        projection_type: str,
        center_entity_id: str,
        hops: int,
    ) -> dict[str, list[dict[str, object]]]:
        """M-0.2 §4.1.2 — N-hop neighborhood of a graph projection (concept_graph / task_graph).

        Read-triggers-catch-up (§4.2), then BFS `hops` out from `center_entity_id` over *active*
        edges, traversed undirected (邻域 is reachability, not direction). hops=0 returns just the
        center node (no edges). Unknown center → empty neighborhood. Returns the induced subgraph
        {"nodes": [...], "edges": [...]} taken verbatim from the projection.
        """
        if projection_type not in _NEIGHBORHOOD_SCHEMA:
            msg = (
                f"get_neighborhood only supports graph projections "
                f"{sorted(_NEIGHBORHOOD_SCHEMA)}, got {projection_type!r}"
            )
            raise ValueError(msg)
        self.catchup(event_log)  # §4.2 read-triggers-catch-up
        state = self.read(projection_type) or {}
        node_key, src_key, dst_key = _NEIGHBORHOOD_SCHEMA[projection_type]
        raw_nodes = _as_list(state.get("nodes"))
        raw_edges = _as_list(state.get("edges"))
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in raw_edges:
            if edge.get("is_active") is False:
                continue  # removed edges are not traversable
            src = edge.get(src_key)
            dst = edge.get(dst_key)
            if isinstance(src, str) and isinstance(dst, str) and src and dst:
                adjacency[src].add(dst)
                adjacency[dst].add(src)
        visited: set[str] = {center_entity_id}
        frontier: set[str] = {center_entity_id}
        for _ in range(max(0, hops)):
            next_frontier: set[str] = set()
            for node_id in frontier:
                for neighbor in adjacency.get(node_id, ()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
            if not next_frontier:
                break
            frontier = next_frontier
        nodes = [n for n in raw_nodes if n.get(node_key) in visited]
        edges = [
            e
            for e in raw_edges
            if e.get("is_active") is not False
            and e.get(src_key) in visited
            and e.get(dst_key) in visited
        ]
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def get_entity_history(
        event_log: EventLog,
        entity_type: str,
        entity_id: str,
    ) -> list[dict[str, object]]:
        """M-0.2 §4.1.3 — full history of an entity via the M-0.1 subjects[] entity index.

        Returns [{event_id, event_type, timestamp, summary}] in sequence order (oldest→newest).
        summary is a short event_type + payload-derived hint (best-effort, never raises). Reads the
        event log directly (no projection state involved), so no catch-up is needed.
        """
        records = event_log.get_events_by_entity(entity_type, entity_id)
        records.sort(key=lambda r: r.sequence_number)
        return [
            {
                "event_id": r.event_id,
                "event_type": r.event_type.value,
                "timestamp": r.timestamp.isoformat(),
                "summary": _entity_history_summary(r),
            }
            for r in records
        ]

    @staticmethod
    def get_supersede_chain(
        event_log: EventLog,
        entity_type: str,
        entity_id: str,
    ) -> list[dict[str, object]]:
        """M-0.2 §4.1.4 — supersede / evolution chain of an entity (versions newest→oldest).

        Resolves the entity's events (subjects[] index), takes the most-recent event as the chain
        head, then walks supersede.superseded_event_id (M-0.1 supersede.idx). Each returned version
        carries {event_id, event_type, sequence_number, is_supersede, superseded_event_id}. An
        entity with no events → []. An entity whose head has no supersede link → a single-version
        chain (the head itself).
        """
        records = event_log.get_events_by_entity(entity_type, entity_id)
        if not records:
            return []
        records.sort(key=lambda r: r.sequence_number)
        head = records[-1]
        chain = event_log.get_supersede_chain(head.event_id)
        return [
            {
                "event_id": rec.event_id,
                "event_type": rec.event_type.value,
                "sequence_number": rec.sequence_number,
                "is_supersede": rec.supersede.is_supersede,
                "superseded_event_id": rec.supersede.superseded_event_id,
            }
            for rec in chain
        ]

    def get_watermark(self, projection_type: str) -> int:
        """M-0.2 §4.1.5 — current consumed watermark (event sequence_number), NO catch-up.

        Returns the persisted projection's meta.watermark when present (Patch 3 — the authoritative
        recovery position), else the global consumer cursor minus one (the position catch-up last
        advanced to). Does not trigger catch-up (§4.1.5 explicit).
        """
        if projection_type not in _PROJECTION_FILES:
            msg = f"unknown projection: {projection_type}"
            raise ValueError(msg)
        meta = self._read_meta(projection_type)
        if meta is not None:
            wm = meta.get("watermark")
            if isinstance(wm, int):
                return wm
        return max(0, self.cursor - 1)

    # ─── Patch 3 atomic projection persistence + recovery (M-0.2 附录B Patch 3) ──
    # The flat layout stores meta as a sidecar `graph/<name>.meta.json` rather than the §6.1
    # per-projection-subdir `graph/<name>/meta.json` (the materialized projections are flat files,
    # not subdirs). The Patch 3 semantics — state_hash + watermark + generation written atomically
    # (tmp → fsync → rename → dir fsync) so a torn state/meta pair is detectable, and a recovery
    # protocol that rebuilds on mismatch — are preserved.

    def _meta_path(self, name: str) -> Path:
        # Hidden sub-dir so meta files never match a `graph/*.json` glob (snapshot bundle dump /
        # validation enumeration use that glob; meta is durability metadata, not a projection).
        return self._dir / ".projection-meta" / f"{_PROJECTION_FILES[name].removesuffix('.json')}.json"

    def _read_meta(self, name: str) -> dict[str, object] | None:
        if name not in _PROJECTION_FILES:
            return None
        path = self._meta_path(name)
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _state_hash(state_text: str) -> str:
        return hashlib.sha256(state_text.encode("utf-8")).hexdigest()

    def _persist_meta(self, name: str, watermark: int) -> None:
        """Atomically write `<name>.meta.json` = {watermark, state_hash, generation} (Patch 3).

        state_hash = hash of the on-disk state.json; generation = prior generation + 1. Written via
        tmp → fsync → rename → dir fsync so meta is durable and atomic w.r.t. the state it describes.
        Skips projections with no materialized state file (nothing to describe yet).
        """
        state_path = self._dir / _PROJECTION_FILES[name]
        if not state_path.exists():
            return
        state_text = state_path.read_text(encoding="utf-8")
        prev = self._read_meta(name)
        generation = 1
        if prev is not None and isinstance(prev.get("generation"), int):
            generation = int(prev["generation"]) + 1
        meta = {
            "projection_type": name,
            "watermark": watermark,
            "state_hash": self._state_hash(state_text),
            "generation": generation,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        meta_path = self._meta_path(name)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        os_replace_with_fsync(tmp, meta_path, meta_path.parent)

    def _persist_all_meta(self, watermark: int, names: Collection[str] | None = None) -> None:
        """Persist meta for `names` (default: every registered projection) at the given watermark.

        catchup() passes only the projections it actually wrote this batch (perf, 顺手窄化) —
        re-hashing and re-persisting meta for the other ~15-20 untouched-but-materialized
        projections on every single catchup() call was pure waste (their on-disk content, and
        therefore their state_hash, did not change). `names=None` keeps the original full-scan
        behavior available for any future caller that wants it (defensive default, no other
        current caller relies on the full scan).
        """
        for name in (names if names is not None else _PROJECTION_FILES):
            self._persist_meta(name, watermark)

    def recover(self, event_log: EventLog) -> dict[str, str]:
        """M-0.2 附录B Patch 3 recovery protocol — make projections consistent after a crash.

        For each projection: if meta + state both present and hash(state) == meta.state_hash, the
        pair is intact (record 'consistent', track its watermark). If meta is present but the state
        hash mismatches (a torn write: state written without meta, or meta written without the new
        state), or the state file vanished, the projection is torn.

        Torn projections are repaired SURGICALLY via ``recompute_one(name)`` — only the torn
        projection's own ``<name>.json`` is discarded and rebuilt from the event log, while the
        global ``.cursor`` and every healthy sibling projection are left byte-for-byte untouched.
        This replaces the old无差别 ``self.rebuild()`` (which reset the global cursor to 0 and
        unlinked all 19 projection files): RUN-095 / project_v3_projection_staleness 实证 a full
        rebuild can REGRESS an otherwise-healthy projection (commit_history 670→612 反退) when only
        one sibling was torn. ``recompute_one`` is the §Patch 3 'rebuild_from_snapshot' equivalent
        scoped to the one projection that actually needs reconstruction.

        Full ``self.rebuild()`` is kept only as the fallback for the two cases where surgery does
        not apply: (a) EVERY materialized projection is torn (no healthy sibling to protect, so a
        global rebuild loses nothing), or (b) a ``recompute_one`` call itself fails (we then fall
        back to the authoritative full replay rather than leave a half-repaired store).

        When everything is intact, the cursor is advanced to the meta watermark if it lags (Patch 3
        — meta.watermark is the recovery authority; advance, do NOT re-apply).

        Returns {projection_name: status} plus an "_action" key
        ('recomputed' / 'rebuilt' / 'advanced' / 'noop'). For 'recomputed', each repaired
        projection keeps its torn diagnosis status ('torn_hash_mismatch' / 'torn_state_missing')
        so callers can see what was repaired; 'rebuilt' is only the all-torn / recompute-failed
        fallback.
        """
        result: dict[str, str] = {}
        torn_names: list[str] = []
        materialized = 0
        max_meta_watermark = -1
        for name in _PROJECTION_FILES:
            meta = self._read_meta(name)
            state_path = self._dir / _PROJECTION_FILES[name]
            if meta is None:
                result[name] = "no_meta"
                continue
            materialized += 1
            if not state_path.exists():
                result[name] = "torn_state_missing"
                torn_names.append(name)
                continue
            actual_hash = self._state_hash(state_path.read_text(encoding="utf-8"))
            if actual_hash == meta.get("state_hash"):
                result[name] = "consistent"
                wm = meta.get("watermark")
                if isinstance(wm, int):
                    max_meta_watermark = max(max_meta_watermark, wm)
            else:
                result[name] = "torn_hash_mismatch"
                torn_names.append(name)
        if torn_names:
            # Every materialized projection torn → no healthy sibling to protect; a full rebuild
            # loses nothing and is the cheaper authoritative path.
            if len(torn_names) == materialized:
                self.rebuild(event_log)
                result["_action"] = "rebuilt"
                return result
            # Partial tear: surgically recompute only the torn projections,保 cursor + healthy
            # siblings untouched. If any recompute_one itself fails, fall back to the full
            # authoritative replay rather than leave a half-repaired store.
            try:
                for name in torn_names:
                    self.recompute_one(name, event_log)
            except Exception:
                self.rebuild(event_log)
                result["_action"] = "rebuilt"
                return result
            result["_action"] = "recomputed"
            return result
        if max_meta_watermark >= 0 and self.cursor < max_meta_watermark + 1:
            # meta.watermark > cursor: persistence succeeded but the cursor advert lagged — advance
            # the cursor to meta.watermark (do NOT re-apply events, Patch 3 关键澄清).
            self._advance_cursor(max_meta_watermark + 1)
            result["_action"] = "advanced"
        else:
            result["_action"] = "noop"
        return result

    def validate_rebuild(self, event_log: EventLog) -> tuple[int, list[str]]:
        """T-L3kc-07 — projection 完整性自查 (债自查机制本体, M-3.1 §9.3).

        Backup the currently-materialized projection files, full-replay rebuild from the event
        log (the authoritative truth), then compare each projection's stored content against the
        rebuilt content. Any projection whose stored state diverged from what the events imply is
        returned as inconsistent (the caller emits a projection_inconsistency FindingCreated). The
        rebuilt (corrected) state is what stays on disk; the pre-rebuild snapshot is kept under
        graph/.validate-backup/ for audit.

        Returns (events_replayed, inconsistent_projection_names).
        """
        before = {name: self.read(name) for name in _PROJECTION_FILES}
        backup_dir = self._dir / ".validate-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for fname in _PROJECTION_FILES.values():
            src = self._dir / fname
            if src.exists():
                (backup_dir / fname).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        processed = self.rebuild(event_log)
        inconsistent = [
            name
            for name in _PROJECTION_FILES
            if _canonical_json(before[name]) != _canonical_json(self.read(name))
        ]
        return processed, inconsistent

    # ─── obligation_lifecycle reducer (M-0.2 §3.3 + Patch M-0.6-E) ─────────

    def _reduce_obligation_lifecycle(self, ev: EventRecord) -> None:
        existing = self.read("obligation_lifecycle_state")
        if existing is None:
            existing = {"obligations": []}
        obligations_data = existing.get("obligations", [])
        if not isinstance(obligations_data, list):
            obligations_data = []
        # Convert to typed model for easier manipulation
        nodes: list[dict[str, object]] = list(obligations_data)  # already list[dict]
        payload = ev.payload
        obligation_id = payload.get("obligation_id")
        if not isinstance(obligation_id, str):
            return
        existing_node = next(
            (n for n in nodes if n.get("obligation_id") == obligation_id),
            None,
        )
        if ev.event_type is EventType.OBLIGATION_CAPTURED:
            node = self._build_obligation_node_from_captured(ev, payload, obligation_id)
            if existing_node is None:
                nodes.append(node)
        elif existing_node is not None:
            self._update_obligation_node_state(ev, existing_node)
        self.write_atomic("obligation_lifecycle_state", {"obligations": nodes})

    @staticmethod
    def _build_obligation_node_from_captured(
        ev: EventRecord,
        payload: dict[str, object],
        obligation_id: str,
    ) -> dict[str, object]:
        attached_to_raw = payload.get("attached_to", [])
        if not isinstance(attached_to_raw, list):
            attached_to: list[dict[str, object]] = []
        else:
            attached_to = [
                {"entity_type": e.get("entity_type"), "entity_id": e.get("entity_id")}
                for e in attached_to_raw
                if isinstance(e, dict)
            ]
        node: dict[str, object] = {
            "obligation_id": obligation_id,
            "lifecycle_state": ObligationFieldsLifecycleState.CAPTURED.value,
            # T-L0-34 (波1) Patch E §3.4: 诚实名同步 lifecycle_state (消 canonical/scoped 混淆)。
            "last_observed_lifecycle_event_type": ObligationFieldsLifecycleState.CAPTURED.value,
            "canonical_state": ObligationCanonicalState.ACTIVE.value,  # Narrow Patch E
            "attached_to": attached_to,
            "scope_rule": payload.get("scope_rule", ""),
            "scope_type": payload.get("scope_type", ObligationScopeType.GLOBAL.value),
            "severity": payload.get("severity", ObligationSeverity.NORMAL.value),
            "nature": payload.get("nature", ObligationNature.INVARIANT.value),
            "definition": payload.get("definition", ""),
            "created_at_event_id": ev.event_id,
            "last_state_change_event_id": ev.event_id,
            # T-L0-33 (波1) Patch E §13.1 — 三分类字段, 各指对应类型最新事件 (区分 activation/check/
            # violation, 不再都塞进单一 last_state_change_event_id)。captured 时未发生 → None。
            "last_activation_event_id": None,
            "last_check_event_id": None,
            "last_violation_event_id": None,
            "superseded_by": None,
        }
        # M-0.6 §2.1 scope_hint — mechanical-filter assist (concept_anchors for
        # concept_neighborhood obligations). Flows ObligationCaptured payload → projection
        # node → M-0.3 §2.3 anchors∩neighborhood. Only carried when present (bootstrap
        # GLOBAL/ALL_PATCHES obligations omit it).
        scope_hint = payload.get("scope_hint")
        if isinstance(scope_hint, dict):
            node["scope_hint"] = scope_hint
        return node

    @staticmethod
    def _update_obligation_node_state(ev: EventRecord, node: dict[str, object]) -> None:
        node["last_state_change_event_id"] = ev.event_id
        # T-L0-33 (波1) Patch E §13.1 — 三分类字段各记自己类型的最新事件 id (新的覆盖旧的 → "最新")。
        if ev.event_type is EventType.OBLIGATION_ACTIVATED:
            node["lifecycle_state"] = ObligationFieldsLifecycleState.ACTIVATED.value
            node["last_activation_event_id"] = ev.event_id
        elif ev.event_type is EventType.OBLIGATION_CHECKED:
            node["lifecycle_state"] = ObligationFieldsLifecycleState.CHECKED.value
            node["last_check_event_id"] = ev.event_id
        elif ev.event_type is EventType.OBLIGATION_VIOLATED:
            node["lifecycle_state"] = ObligationFieldsLifecycleState.VIOLATED.value
            node["last_violation_event_id"] = ev.event_id
        elif ev.event_type is EventType.OBLIGATION_EVOLVED:
            node["lifecycle_state"] = ObligationFieldsLifecycleState.EVOLVED.value
            # Cap1 (RUN-038, M-0.6 §3.2): only apply active → superseded when legal.
            # If already terminal (superseded / retired) the canonical_state is NOT
            # re-transitioned (terminal states cannot be revived / cross-moved); the
            # scoped audit trail (last_observed_lifecycle_event_type) below still records
            # that the event was observed.
            _apply_canonical_transition(node, ObligationCanonicalState.SUPERSEDED)
        elif ev.event_type is EventType.OBLIGATION_RETIRED:
            node["lifecycle_state"] = ObligationFieldsLifecycleState.RETIRED.value
            _apply_canonical_transition(node, ObligationCanonicalState.RETIRED)
        # T-L0-34 (波1) Patch E §3.4: 诚实名同步 lifecycle_state (scoped 事件 activated/checked/violated
        # 更新"最近观测事件类型", canonical_state 只被 evolved/retired 这类 canonical 转移改 — 二者不混)。
        node["last_observed_lifecycle_event_type"] = node["lifecycle_state"]

    # ─── commit_history reducer ─────────────────────────────────────────────

    def _reduce_commit_history(self, ev: EventRecord, *, verdict: str) -> None:
        existing = self.read("commit_history") or {"commits": []}
        commits_data = existing.get("commits", [])
        if not isinstance(commits_data, list):
            commits_data = []
        commits: list[dict[str, object]] = list(commits_data)
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        commits.append(
            {
                "commit_event_id": ev.event_id,
                "envelope_event_id": payload.get("envelope_event_id", ""),
                "verdict": verdict,
                "occurred_at_event_id": ev.event_id,
            },
        )
        self.write_atomic("commit_history", {"commits": commits})

    # ─── gate_check_firing reducer (T-FU-09 可观测) ──────────────────────────

    def _reduce_gate_check_firing(self, ev: EventRecord, *, verdict: str) -> None:
        """commit gate 每道 check 的触发计数 — 从 CommitAccepted.checks_passed / CommitRejected.
        failure_reason 累计。

        accepted 路径: 遍历 checks_passed[{check_type, result}], 累计 by_check_type[check_type][result]
        (result ∈ passed / not_applicable / would_reject_observed / ...)。
        rejected 路径: gate 首拒短路 — 拒批的那道 check 不进 checks_passed, 真因落在
        rejection_reasons[{check_type, failure_reason, evidence}] (assemble_rejected_batch 的
        payload 结构), 故遍历它按 failure_reason 累计 rejections_by_reason[reason]。

        live-target-execution-evidence@v1 goal 收口门的运行观测语义由此可算:
          · 空放行 (潜伏) = by_check_type.live_target_reconciled.not_applicable
          · 真评估通过     = by_check_type.live_target_reconciled.passed
          · 真拒 (承重开火) = rejections_by_reason.{goal_live_target_unsatisfied, goal_completion_no_gsid}
        """
        existing = self.read("gate_check_firing") or {}
        by_check_type = existing.get("by_check_type")
        if not isinstance(by_check_type, dict):
            by_check_type = {}
        rejections_by_reason = existing.get("rejections_by_reason")
        if not isinstance(rejections_by_reason, dict):
            rejections_by_reason = {}
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state") if isinstance(payload.get("after_state"), dict) else {}

        if verdict == "accepted":
            checks = payload.get("checks_passed")
            if not isinstance(checks, list):
                checks = after.get("checks_passed") if isinstance(after, dict) else None
            if isinstance(checks, list):
                for c in checks:
                    if not isinstance(c, dict):
                        continue
                    ct = c.get("check_type")
                    res = c.get("result")
                    if not isinstance(ct, str) or not isinstance(res, str):
                        continue
                    bucket = by_check_type.get(ct)
                    if not isinstance(bucket, dict):
                        bucket = {}
                    bucket[res] = int(bucket.get(res, 0)) + 1
                    by_check_type[ct] = bucket
        else:  # rejected
            reasons = payload.get("rejection_reasons")
            if not isinstance(reasons, list):
                reasons = after.get("rejection_reasons") if isinstance(after, dict) else None
            captured = False
            if isinstance(reasons, list):
                for item in reasons:
                    if not isinstance(item, dict):
                        continue
                    reason = item.get("failure_reason") or item.get("check_type")
                    if not isinstance(reason, str) or not reason:
                        continue
                    rejections_by_reason[reason] = int(rejections_by_reason.get(reason, 0)) + 1
                    captured = True
            if not captured:
                # rejection_reasons 缺失/空 (旧形态或异常) — 诚实记一条 <unspecified>, 不静默丢。
                rejections_by_reason["<unspecified>"] = int(rejections_by_reason.get("<unspecified>", 0)) + 1

        self.write_atomic(
            "gate_check_firing",
            {"by_check_type": by_check_type, "rejections_by_reason": rejections_by_reason},
        )

    # ─── debt_registry reducer (RUN-029 第0波 ③) ─────────────────────────────

    def _reduce_debt_registry(self, ev: EventRecord) -> None:
        """DebtRegistered → upsert an open debt node; DebtResolved → mark it resolved.

        This is the honest-done-ledger seed (phase-status 升级形态): the materialized set
        of "what the system openly owes" derived purely from real events. Idempotent — a
        re-registered debt_id replaces in place; resolve sets state without dropping history
        (the resolving event id is recorded). Tolerant of payloads that omit optional fields.
        """
        existing = self.read("debt_registry") or {"debts": []}
        debts_data = existing.get("debts", [])
        if not isinstance(debts_data, list):
            debts_data = []
        debts: list[dict[str, object]] = list(debts_data)
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        debt_id = payload.get("debt_id")
        if not isinstance(debt_id, str) or not debt_id:
            return
        node = next((d for d in debts if d.get("debt_id") == debt_id), None)
        if ev.event_type is EventType.DEBT_REGISTERED:
            # activation-debt@v1: activation 债登记时自带 state=awaiting_organic_traffic(真空期,
            # 不算失败) + expiry/gap_type/root_cause_key。历史/普通债 payload 无 state 键 → 仍默认
            # "open"(behaviour 不变)。新字段缺省 None, 节点键确定性(无 time/uuid), 保幂等 rebuild。
            registered = {
                "debt_id": debt_id,
                "debt_type": payload.get("debt_type"),
                "severity": payload.get("severity"),
                "title": payload.get("title"),
                # description 此前不物化 —— activation-debt@v1 生命周期评估器 (T-ACT-6) 再登记转态时
                # 需从投影节点忠实取回全部载体字段 (含 description, DebtRegisteredPayload 必填), 故补上。
                # 历史/普通债的 DebtRegistered 同样必带 description → rebuild 后向兼容 (旧节点重放即补齐)。
                "description": payload.get("description"),
                "against_capability": payload.get("against_capability"),
                "depends_on": payload.get("depends_on", []),
                "resolution_criteria": payload.get("resolution_criteria"),
                "state": payload.get("state") or "open",
                "gap_type": payload.get("gap_type"),
                "expiry": payload.get("expiry"),
                "root_cause_key": payload.get("root_cause_key"),
                "registered_by_task": payload.get("registered_by_task"),
                "registered_by_event_id": ev.event_id,
                "resolved_by_event_id": None,
                "resolution_evidence": None,
                # activation-debt@v1 生命周期评估器 (T-ACT-6): inv2 状态机转换
                # (awaiting_organic_traffic → resolved / → expired_escalated) 由带 debt_id 的
                # 再登记驱动 (register_activation_debt 幂等 upsert)。首次登记无转换 → None; 转换发生
                # 时下面对 activation 债改写为转换事件 id, 供追溯"债何时被评估器转态"。
                "state_transitioned_by_event_id": None,
            }
            if node is None:
                debts.append(registered)
            else:
                # activation 债的再登记 = 状态机转换 (不是重复登记): 保留【首次登记】的 event_id
                # (否则 node.update 会用转换事件覆盖它、丢失债诞生锚点), 并把转换事件 id 记进
                # state_transitioned_by_event_id 供单向追溯。普通债 (stub/deferral/…) 的再登记行为
                # 保持不变 (整体 replace-in-place), 后向兼容不动其观测形态。
                if payload.get("debt_type") == DebtType.ACTIVATION.value:
                    registered["registered_by_event_id"] = (
                        node.get("registered_by_event_id") or ev.event_id
                    )
                    registered["state_transitioned_by_event_id"] = ev.event_id
                node.update(registered)
        elif ev.event_type is EventType.DEBT_RESOLVED:
            if node is None:
                # resolve of an unknown debt — record a tombstone so it is not silently lost
                node = {"debt_id": debt_id, "state": "resolved"}
                debts.append(node)
            node["state"] = "resolved"
            node["resolved_by_event_id"] = ev.event_id
            node["resolution_evidence"] = payload.get("resolution_evidence")
        self.write_atomic("debt_registry", {"debts": debts})

    # ─── consumer_relation reducer (T-L0-09 波1 — M-0.2 §3.6) ────────

    def _reduce_consumer_relation(self, ev: EventRecord) -> None:
        """M-0.2 §3.6 — 物化每个 concept 的最新消费方列表 (替换语义, T-L0-09)。

        ConsumerListPublished(after_state.{concept_id, consumers}) → 用最新发布的 consumers
        **替换** 该 concept_id 的整条关系 (非追加)。此前 consumer_relation 在 _PROJECTION_FILES
        声明但无 reducer 分支 → 从不物化 (stub)。consumers 按当前 producer 真形存
        (consumer_type/consumer_id/usage); 与 spec §3.1 ConsumerRef 字段名分歧是既有
        producer-alignment exempt 债 (CONSUMER_LIST_PUBLISHED 在 PAYLOAD_VALIDATION_EXEMPT)。
        Idempotent: 同 concept_id 替换在位, full rebuild 重放 state 一致。
        """
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after_state = after if isinstance(after, dict) else {}
        concept_id = after_state.get("concept_id")
        if not isinstance(concept_id, str) or not concept_id:
            return
        consumers_raw = after_state.get("consumers", [])
        consumers = [c for c in consumers_raw if isinstance(c, dict)] if isinstance(consumers_raw, list) else []
        existing = self.read("consumer_relation") or {"relations": []}
        rels_data = existing.get("relations", [])
        if not isinstance(rels_data, list):
            rels_data = []
        relations: list[dict[str, object]] = [r for r in rels_data if isinstance(r, dict)]
        node = next((r for r in relations if r.get("concept_id") == concept_id), None)
        if node is None:
            relations.append({"concept_id": concept_id, "consumers": consumers})
        else:
            node["consumers"] = consumers  # 替换语义 (最新发布覆盖)
            node["concept_id"] = concept_id
        self.write_atomic("consumer_relation", {"relations": relations})

    # ─── engineering_consensus_index reducer (M-1.2 §10.2 Patch T / T-L1-15 波3) ──

    def _reduce_engineering_consensus_index(self, ev: EventRecord) -> None:
        """M-1.2 §10.2 Patch T — per-plan 批次冻结状态 (Patch T 第 3 个 reducer, T-L1-15).

        Patch T 设计了 4 个 projection: concept_graph (F-019-8 已做) / at_reference_graph
        (T-L0-08 已做) / consumer_lists (= consumer_relation M-0.2 §3.6, T-L0-09 已做) /
        engineering_consensus_index (此前完全缺 — EngineeringConsensusFreezed emit 了但无
        projection 物化 → "某 plan 冻到第几批 / 哪些 concept 已冻 / 共识完成没" 不可查)。

        EngineeringConsensusFreezed(after_state.{plan_id, batch_info, frozen_concept_ids,
        concept_graph_snapshot_hash, freezed_at_event_offset, invariants?}) →
        per-plan batches[] 加/替换一条批次记录 + plan-level rollup
        (latest_batch_number / is_consensus_complete = 见过 is_final_batch=true 的批)。

        Dedup by (plan_id, batch_number): 同批 freeze 重放替换在位 → full rebuild 重放 state
        一致 (§6 idempotent)。批次 record 含本批 §3.7 invariants (T-L1-21 — freeze 带不变量时)。
        """
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        plan_id = after.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            return
        batch_info_raw = after.get("batch_info")
        batch_info = batch_info_raw if isinstance(batch_info_raw, dict) else {}
        batch_number = batch_info.get("batch_number")
        if not isinstance(batch_number, int):
            batch_number = 1
        is_final = bool(batch_info.get("is_final_batch", False))
        frozen = [str(c) for c in after.get("frozen_concept_ids", []) if isinstance(c, (str, int))]
        invariants_raw = after.get("invariants")
        invariants = [iv for iv in invariants_raw if isinstance(iv, dict)] if isinstance(invariants_raw, list) else []

        batch_record = {
            "batch_number": batch_number,
            "is_final_batch": is_final,
            "batch_scope": batch_info.get("batch_scope", ""),
            "frozen_concept_ids": frozen,
            "invariants": invariants,
            "concept_graph_snapshot_hash": after.get("concept_graph_snapshot_hash", ""),
            "freezed_at_event_offset": after.get("freezed_at_event_offset"),
            "consensus_session_id": after.get("consensus_session_id", ""),
            "frozen_at_event_id": ev.event_id,
        }

        def _bn(b: dict[str, object]) -> int:
            v = b.get("batch_number")
            return v if isinstance(v, int) else 0

        existing = self.read("engineering_consensus_index") or {}
        plans_data = existing.get("plans")
        plans: list[dict[str, object]] = (
            [p for p in plans_data if isinstance(p, dict)] if isinstance(plans_data, list) else []
        )
        plan_node = next((p for p in plans if p.get("plan_id") == plan_id), None)
        if plan_node is None:
            plan_node = {"plan_id": plan_id, "batches": []}
            plans.append(plan_node)
        batches_data = plan_node.get("batches")
        batches: list[dict[str, object]] = (
            [b for b in batches_data if isinstance(b, dict)] if isinstance(batches_data, list) else []
        )
        # dedup by batch_number — re-replay of the same batch replaces in place (idempotent §6).
        batches = [b for b in batches if b.get("batch_number") != batch_number]
        batches.append(batch_record)
        batches.sort(key=_bn)
        plan_node["batches"] = batches
        # plan-level rollup recomputed from all batches each time (idempotent).
        plan_node["latest_batch_number"] = max((_bn(b) for b in batches), default=0)
        plan_node["is_consensus_complete"] = any(bool(b.get("is_final_batch")) for b in batches)
        self.write_atomic("engineering_consensus_index", {"plans": plans})

    # ─── capability_status reducer (T-TRACK-01 — event-sourced 603 board) ────────

    def _reduce_capability_status(self, ev: EventRecord) -> None:
        """T-TRACK-01 — derive the 603-capability board from events (治 phase-status-not-a-projection).

        CapabilityBaselineIngested → seed/refresh every node from the panorama baseline. The
        baseline_classification is recorded immutably; current_classification seeds to baseline
        but is PRESERVED across re-ingestion once an advance has moved it (a later panorama
        refresh must not clobber recorded progress).
        CapabilityStatusAdvanced → flip one node's current_classification (the landing that
        moved it), recording advanced_by / evidence / the moving event id.

        Idempotent: ingest-then-advance replays to the same state (advance always post-dates the
        baseline it depends on in the log, so full rebuild reproduces identical current state).
        """
        existing = self.read("capability_status") or {"capabilities": []}
        caps_data = existing.get("capabilities", [])
        if not isinstance(caps_data, list):
            caps_data = []
        caps: list[dict[str, object]] = [c for c in caps_data if isinstance(c, dict)]
        by_id = {c.get("capability_id"): c for c in caps}
        payload = ev.payload if isinstance(ev.payload, dict) else {}

        if ev.event_type is EventType.CAPABILITY_BASELINE_INGESTED:
            entries = payload.get("capabilities", [])
            if not isinstance(entries, list):
                entries = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                cid = entry.get("capability_id")
                if not isinstance(cid, str) or not cid:
                    continue
                baseline = entry.get("classification")
                prior = by_id.get(cid)
                # preserve an already-recorded advance across a baseline re-ingest
                if prior is not None and prior.get("advanced_by_event_id"):
                    current = prior.get("current_classification")
                else:
                    current = baseline
                seeded: dict[str, object] = {
                    "capability_id": cid,
                    "module": entry.get("module"),
                    "name": entry.get("name"),
                    "baseline_classification": baseline,
                    "current_classification": current,
                    "linked_debts": entry.get("linked_debts", []),
                    "baseline_ingested_by_event_id": ev.event_id,
                    "advanced_by": prior.get("advanced_by") if prior else None,
                    "advanced_by_event_id": prior.get("advanced_by_event_id") if prior else None,
                    "advance_evidence": prior.get("advance_evidence") if prior else None,
                    # RUN-096 — preserve an already-recorded enforcement_level across re-ingest
                    "enforcement_level": prior.get("enforcement_level") if prior else None,
                }
                if prior is None:
                    caps.append(seeded)
                    by_id[cid] = seeded
                else:
                    prior.update(seeded)
        elif ev.event_type is EventType.CAPABILITY_STATUS_ADVANCED:
            cid = payload.get("capability_id")
            if not isinstance(cid, str) or not cid:
                return
            node = by_id.get(cid)
            if node is None:
                # advance of a capability not in the baseline — record a node so it is not lost
                node = {
                    "capability_id": cid,
                    "module": None,
                    "name": None,
                    "baseline_classification": payload.get("from_classification"),
                    "linked_debts": [],
                    "baseline_ingested_by_event_id": None,
                }
                caps.append(node)
                by_id[cid] = node
            node["current_classification"] = payload.get("to_classification")
            node["advanced_by"] = payload.get("advanced_by")
            node["advanced_by_event_id"] = ev.event_id
            node["advance_evidence"] = payload.get("evidence_ref")
            # RUN-096 — orthogonal enforcement_level: set when this advance marks it (evidence-backed);
            # an advance that omits it never downgrades an already-recorded level (preserve).
            enforcement = payload.get("enforcement_level")
            if enforcement is not None:
                node["enforcement_level"] = enforcement
            else:
                node.setdefault("enforcement_level", None)
        self.write_atomic("capability_status", {"capabilities": caps})

    # ─── finding_lifecycle reducer (M-0.2 §3.10 / RUN-035 T-L1-52/53) ────────

    def _reduce_finding_lifecycle(self, ev: EventRecord) -> None:
        """M-0.2 §3.10 finding_lifecycle 三态生命周期 (RUN-035 T-L1-52/53; 重叠 T-L1-64).

        FindingCreated → created; FindingVerified → verified (+verification_state 三态);
        FindingDisputed → disputed; FindingResolved → resolved (+resolution +closure_state)。
        review 三态/closure 事件 emit 后真归约进 projection (否则 "事件发了没人读" 假完整)。
        Idempotent: 同 finding_id 在位 upsert, full rebuild 重放 state 一致。
        """
        existing = self.read("finding_lifecycle") or {"findings": []}
        findings_data = existing.get("findings", [])
        findings: list[dict[str, object]] = list(findings_data) if isinstance(findings_data, list) else []
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        # canonical finding 事件 payload 是 flat top-level; 容错 after_state 嵌套形状。
        after = payload.get("after_state")
        src = after if isinstance(after, dict) else payload
        finding_id = src.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id:
            return
        node = next((f for f in findings if f.get("finding_id") == finding_id), None)
        # T-LND-01 (INV-B1-1): review_unit_id 是下游 derived-review-verdict 折叠的分组键 =
        # review session_id (一次 review run 即一个 review-unit)。payload 显式优先 (review CLI
        # 盖 sid); 回退 provenance.session_id —— review finding 的 provenance 带 session_id (审计
        # 实证), gate (T-LND-04) 据 TaskRunCompleted.provenance.session_id join verdict。回退仅对
        # review 语境 (source.type=model_review), 避免给系统/execution finding 误贴分组键。缺则
        # None (不参与 verdict 折叠)。
        review_unit_id = src.get("review_unit_id")
        if not isinstance(review_unit_id, str) or not review_unit_id:
            source = src.get("source")
            is_review = isinstance(source, dict) and source.get("type") == "model_review"
            sess = getattr(ev.provenance, "session_id", None)
            review_unit_id = sess if (is_review and isinstance(sess, str) and sess) else None
        if ev.event_type is EventType.FINDING_CREATED:
            created = {
                "finding_id": finding_id,
                "lifecycle_state": "created",
                "severity": src.get("severity"),
                "risk_surface": src.get("risk_surface"),
                "target": src.get("target"),
                "review_unit_id": review_unit_id,
                "detection_method": src.get("detection_method"),
                "review_dimension": src.get("review_dimension"),
                "is_preexisting": src.get("is_preexisting"),
                "created_at_event_id": ev.event_id,
                "last_state_change_event_id": ev.event_id,
                "related_patch_event_id": src.get("related_patch_event_id"),
                "verification_state": None,
                "resolution": None,
                "closure_state": None,
            }
            if node is None:
                findings.append(created)
            else:
                # 乱序保护: 先到的 VERIFIED/DISPUTED 已为节点定了 review_unit_id 时, 不被后到
                # 的 CREATED(本条无显式/可派生 review_unit_id) 覆盖成 None。
                if not created.get("review_unit_id") and node.get("review_unit_id"):
                    created["review_unit_id"] = node["review_unit_id"]
                node.update(created)
        elif ev.event_type is EventType.FINDING_VERIFIED:
            if node is None:
                node = {"finding_id": finding_id, "created_at_event_id": ev.event_id, "review_unit_id": review_unit_id}
                findings.append(node)
            node["lifecycle_state"] = "verified"
            node["verification_state"] = src.get("verification_state")
            node["last_state_change_event_id"] = ev.event_id
        elif ev.event_type is EventType.FINDING_DISPUTED:
            if node is None:
                node = {"finding_id": finding_id, "created_at_event_id": ev.event_id, "review_unit_id": review_unit_id}
                findings.append(node)
            node["lifecycle_state"] = "disputed"
            node["last_state_change_event_id"] = ev.event_id
        elif ev.event_type is EventType.FINDING_RESOLVED:
            if node is None:
                node = {"finding_id": finding_id, "created_at_event_id": ev.event_id, "review_unit_id": review_unit_id}
                findings.append(node)
            node["lifecycle_state"] = "resolved"
            node["resolution"] = src.get("resolution")
            closure = src.get("closure_verification")
            if isinstance(closure, dict):
                node["closure_state"] = closure.get("closure_state")
            node["last_state_change_event_id"] = ev.event_id
        elif ev.event_type is EventType.FINDING_ACCEPTED:
            # f-escalation-task-oriented: 改不了/不可变 finding 被 owner accept 为已知基线 →
            # ACCEPTED 终态。哨兵每轮先查 accepted 集跳过它, 永不再报 (不刷屏)。
            if node is None:
                node = {"finding_id": finding_id, "created_at_event_id": ev.event_id, "review_unit_id": review_unit_id}
                findings.append(node)
            node["lifecycle_state"] = "accepted"
            node["accepted_reason"] = src.get("accepted_reason")
            node["immutable_baseline"] = src.get("immutable_baseline")
            node["accepted_signature"] = src.get("accepted_signature")
            node["last_state_change_event_id"] = ev.event_id
        elif ev.event_type is EventType.FINDING_CLOSURE_CONTRACT_AMENDED:
            # f-stale-closure-contract-permanently-unclosable: 修订 closure_contract。**刻意不动**
            # lifecycle_state / last_state_change_event_id —— amend 不是状态迁移 (一条 verified
            # finding 换了合约锚点后仍是 verified; 把它写成迁移会让 finding-resolve 的"未 resolved"
            # 前置检查、哨兵的状态判读、review verdict 折叠全部误读)。只登记"生效合约在哪条事件上"。
            if node is None:
                # 乱序重放兜底: amend 先到 → 建无状态占位节点, 待 CREATED 重放时 update 填状态。
                node = {"finding_id": finding_id, "review_unit_id": review_unit_id}
                findings.append(node)
            node["closure_contract_amended_event_id"] = ev.event_id
            _prev_amends = node.get("closure_contract_amend_count")
            node["closure_contract_amend_count"] = (
                _prev_amends + 1 if isinstance(_prev_amends, int) else 1
            )
        self.write_atomic("finding_lifecycle", {"findings": findings})

    # ─── detection_rule_lifecycle reducer (M-0.2 §3.11 / T-L0-11 波1) ─────────

    def _reduce_detection_rule(self, ev: EventRecord) -> None:
        """M-0.2 §3.11 detection_rule_lifecycle — upsert by rule_id (此前 stub: 声明 projection
        文件但无 reducer 分支 → DetectionRule* 事件发了没人读, 假完整)。

        DetectionRuleProposed → proposed (+rule_definition); Shadowing → shadow; Promoted →
        active_warning/enforced (取 payload.rule_lifecycle_state); Retired → retired。lifecycle_state
        直取 payload.rule_lifecycle_state (各 payload 自带该 Literal)。payload flat (无 after_state
        wrapper); 容错嵌套形。Idempotent: 同 rule_id 在位 upsert, full rebuild 重放 state 一致。
        """
        existing = self.read("detection_rule_lifecycle") or {"rules": []}
        rules_data = existing.get("rules", [])
        rules: list[dict[str, object]] = list(rules_data) if isinstance(rules_data, list) else []
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        src = after if isinstance(after, dict) else payload
        rule_id = src.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            return
        state = src.get("rule_lifecycle_state")
        node = next((r for r in rules if r.get("rule_id") == rule_id), None)
        if node is None:
            node = {
                "rule_id": rule_id,
                "rule_definition": src.get("rule_definition", ""),
                "shadow_stats": {},
                "created_at_event_id": ev.event_id,
            }
            rules.append(node)
        if ev.event_type is EventType.DETECTION_RULE_PROPOSED and src.get("rule_definition"):
            node["rule_definition"] = src.get("rule_definition")  # 创建时带定义
        if state is not None:
            node["lifecycle_state"] = state
        node["last_state_change_event_id"] = ev.event_id
        self.write_atomic("detection_rule_lifecycle", {"rules": rules})

    # ─── escalation_lifecycle reducer (M-2.3 §6.1/§6.7 / RUN-038 L0增强) ──────

    # escalation_kind 是 EscalationLifecycleEntry 必填 (min_length=1), 但 producer
    # (towow fix escalate / EscalationRaisedAfter schema) 未发该字段 → producer gap。
    # reducer 侧用诚实占位 "unspecified" 兜底, 不伪造一个假 kind。详汇报挂债。
    _ESCALATION_KIND_PLACEHOLDER = "unspecified"
    # raised_by_skill 必填 (min_length=1)。从 provenance.skill_id 派生; system/test 等
    # 无 skill_id 的事件用占位, 不伪造身份。
    _RAISED_BY_SKILL_PLACEHOLDER = "unknown"

    def _reduce_escalation_lifecycle(self, ev: EventRecord) -> None:
        """M-2.3 §6.7 escalation_lifecycle — 独立 projection (选 B: escalation_id ≠ finding_id,
        状态空间不同, reducer 不塞进 finding reducer)。upsert by escalation_id。

        此前 stub: _PROJECTION_FILES 声明 escalation_lifecycle.json + 头注 Patch M-2.3-E,
        但 _apply 无分支 → Escalation* 事件发了没人读, projection 永不物化 = 假完整。

        ★ RUN-038 L0增强修复: 旧实现把 EscalationRaised 当 flat payload 读 (payload.get(f) 读顶层),
        但真 schema EscalationRaisedPayload (M-1.6 §3.6) 所有字段嵌 after_state (EscalationRaisedAfter);
        真生产者 towow fix escalate 也嵌 after_state 发出 → 旧实现喂真事件时 8 个 nature-facing 富字段
        静默丢失, 且物化节点不符 canonical EscalationLifecycleEntry (缺必填 raised_at/raised_by_skill/
        escalation_kind, lifecycle_state 裸 str, 多 forbidden 字段)。本修复: (1) 全部从 after_state 读;
        (2) 物化节点必符 EscalationLifecycleEntry (写前 model_validate); (3) raised_at 从 ev.timestamp 派生,
        raised_by_skill 从 provenance.skill_id 派生。

        5 状态机 (M-2.3 §6.1) — 全部 after_state 形状:
          EscalationRaised (M-1.6 §3.6, after_state)               → raised
          EscalationTriaged (§3.2, after_state.lifecycle_state)    → triaged  (+triage 分类 +related_*)
          EscalationResolved (§3.3, after_state.lifecycle_state)   → resolved (+resolution +NJ 关联)
          EscalationLearningCaptured (§3.4, after_state)           → post_resolved_learning_captured
          EscalationDecisionMade (§3.5, semantic_judgment)         → 不改 lifecycle (§6.1 resolved 才转移)

        escalation_id: EscalationRaised 取 after_state.escalation_id; transition/judgment 用
        target_entity_id (event-level, 本仓库落 payload), 容错 after_state.escalation_id 嵌套。
        Idempotent: 同 escalation_id 在位 upsert, full rebuild 重放 state 一致。注: M-2.3 daemon 真产
        triage/resolution/learning 事件是 runtime producer (daemon-dependent), 不在本 reducer scope —
        reducer 只机械物化已落地事件。

        ★ M23-F2 / rc-unify-escalation-event-source-materialize-projection — World-B 收进 World-A 同
        schema: GoalEscalationRaised (goal/dead-letter/owner-gate 链, 生产真在用、产量>0) 与其 owner
        NJ answer (NatureJudgmentCaptured 带 responds_to_escalation_event_id) 也物化进**同一个**
        escalation_lifecycle 投影 (向后兼容, 不动 native ESCALATION_* 那套)。这把"久卡 owner 决策落不进
        lifecycle、两套 escalation 世界互不相通" (rc-two-escalation-worlds-no-inbox-materialized) 收口。
        """
        wb_kind, wb_src = _world_b_escalation_event(ev)
        if wb_kind == "GoalEscalationRaised":
            self._materialize_world_b_escalation_raised(ev, wb_src)
            return
        if wb_kind == "NatureJudgmentCaptured":
            self._apply_world_b_escalation_answer(ev, wb_src)
            return
        existing = self.read("escalation_lifecycle") or {"escalations": []}
        escs_data = existing.get("escalations", [])
        escs: list[dict[str, object]] = list(escs_data) if isinstance(escs_data, list) else []
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        # 真 schema: EscalationRaisedAfter.escalation_id 嵌 after_state; transition/judgment 用
        # payload.target_entity_id (本仓库 event-level 落 payload), 容错 after_state.escalation_id。
        escalation_id = (
            after.get("escalation_id") or payload.get("target_entity_id") or payload.get("escalation_id")
        )
        if not isinstance(escalation_id, str) or not escalation_id:
            return
        node = next((e for e in escs if e.get("escalation_id") == escalation_id), None)
        if node is None:
            # canonical EscalationLifecycleEntry 必填占位 — 后续事件按真 schema 填充。
            node = {
                "escalation_id": escalation_id,
                "lifecycle_state": EscalationLifecycleState.RAISED.value,
                "raised_at": ev.timestamp.isoformat(),
                "raised_by_skill": self._RAISED_BY_SKILL_PLACEHOLDER,
                "escalation_kind": self._ESCALATION_KIND_PLACEHOLDER,
            }
            escs.append(node)

        if ev.event_type is EventType.ESCALATION_RAISED:
            node["lifecycle_state"] = EscalationLifecycleState.RAISED.value
            # raised_at 从 ev.timestamp 派生 (producer 未发); raised_by_skill 从 provenance 派生。
            node["raised_at"] = ev.timestamp.isoformat()
            skill_id = ev.provenance.skill_id
            node["raised_by_skill"] = skill_id if (skill_id and skill_id.strip()) else self._RAISED_BY_SKILL_PLACEHOLDER
            # escalation_kind: producer gap — EscalationRaisedAfter 无该字段, 占位兜底 (诚实, 不伪造)。
            node.setdefault("escalation_kind", self._ESCALATION_KIND_PLACEHOLDER)
            # 关联 entity: EscalationRaisedAfter.finding_id / task_id (可空) → related_*_ids 列表。
            finding_id = after.get("finding_id")
            if isinstance(finding_id, str) and finding_id:
                node["related_finding_ids"] = [finding_id]
            task_id = after.get("task_id")
            if isinstance(task_id, str) and task_id:
                node["related_task_ids"] = [task_id]
        elif ev.event_type is EventType.ESCALATION_TRIAGED:
            node["lifecycle_state"] = after.get("lifecycle_state") or EscalationLifecycleState.TRIAGED.value
            node["triaged_at"] = ev.timestamp.isoformat()
            for f in ("triage_category", "triage_reasoning"):
                if f in after:
                    node[f] = after.get(f)
            for rel in (
                "related_finding_ids",
                "related_concept_ids",
                "related_task_ids",
                "related_obligation_ids",
            ):
                val = after.get(rel)
                if isinstance(val, list):
                    node[rel] = val
        elif ev.event_type is EventType.ESCALATION_RESOLVED:
            node["lifecycle_state"] = after.get("lifecycle_state") or EscalationLifecycleState.RESOLVED.value
            node["resolved_at"] = ev.timestamp.isoformat()
            for f in ("resolution", "nature_judgment_event_id"):
                if f in after:
                    node[f] = after.get(f)
        elif ev.event_type is EventType.ESCALATION_LEARNING_CAPTURED:
            node["lifecycle_state"] = EscalationLifecycleState.POST_RESOLVED_LEARNING_CAPTURED.value
            node["learning_captured_at"] = ev.timestamp.isoformat()
            for f in ("learning_type", "seed_for_event_id"):
                if f in after:
                    node[f] = after.get(f)
        elif ev.event_type is EventType.ESCALATION_DECISION_MADE:
            # decision 是 semantic_judgment — §6.1: lifecycle 由 EscalationResolved 落定, decision
            # 不直接驱动 lifecycle_state。canonical EscalationLifecycleEntry 无 decided_state 字段
            # (extra=forbid), 故不在 projection entry 物化 decision 结论 — 该信息走 escalation_decision
            # projection (M-2.3 §3.5), 本 reducer 只确保 escalation 节点存在 (占位已建)。
            pass
        # 写前校验: 物化节点 (JSON-serializable, enum 落字符串值) 必须能被 canonical
        # EscalationLifecycleEntry 接受 (fail-closed — forbidden 字段 / 缺必填 / 非法 enum 值都会
        # 在此抛错, 不静默落坏数据)。strict=False: 接受字符串 enum 值 (projection 持久化形态),
        # 但仍 enforce extra=forbid + 必填 + enum 成员校验。
        EscalationLifecycleEntry.model_validate(node, strict=False)
        self.write_atomic("escalation_lifecycle", {"escalations": escs})

    @staticmethod
    def _world_b_escalation_kind(src: dict[str, object]) -> str:
        """World-B escalation 的 escalation_kind 精确区分 (dead_letter / owner_gate / goal)。

        advisor 点名: 别全填 placeholder, 否则"看着统一其实没信息"。优先读 stub 显式 escalation_kind,
        再按签名字段推导 (dead_letter_entry_id → dead_letter; owner_gate_task_id → owner_gate), 兜底 goal。
        """
        explicit = src.get("escalation_kind")
        if isinstance(explicit, str) and explicit.strip():
            return explicit
        if src.get("dead_letter_entry_id"):
            return "dead_letter"
        if src.get("owner_gate_task_id"):
            return "owner_gate"
        return "goal"

    @staticmethod
    def _world_b_related_task(src: dict[str, object]) -> str | None:
        """World-B escalation 关联的源 task_id (owner-gate / dead-letter task 源)。缺 → None。"""
        for key in ("owner_gate_task_id", "dead_letter_task_id", "task_id"):
            v = src.get(key)
            if isinstance(v, str) and v:
                return v
        # dead-letter 源对象是 task → 取 source_ref (owner 收件箱反查卡的是哪个 task)。
        if src.get("dead_letter_source_type") == "task":
            ref = src.get("dead_letter_source_ref")
            if isinstance(ref, str) and ref:
                return ref
        return None

    @staticmethod
    def _world_b_related_finding(src: dict[str, object]) -> str | None:
        """World-B escalation 关联的源 finding_id (dead-letter finding 源)。缺 → None。"""
        v = src.get("finding_id")
        if isinstance(v, str) and v:
            return v
        if src.get("dead_letter_source_type") == "finding":
            ref = src.get("dead_letter_source_ref")
            if isinstance(ref, str) and ref:
                return ref
        return None

    def _materialize_world_b_escalation_raised(
        self, ev: EventRecord, src: dict[str, object],
    ) -> None:
        """M23-F2: GoalEscalationRaised (World-B) 物化进 escalation_lifecycle (World-A 同 schema)。

        键 = ev.event_id —— 这是 owner NJ 的 responds_to_escalation_event_id 所指 (与
        orchestrator._pending_escalations_from 同款匹配契约: 两族 id 形态归一为 escalation 的 event_id),
        使 owner 答复能闭环 resolve。Idempotent: 同 event_id 在位 upsert, full rebuild 重放一致。
        """
        escalation_event_id = ev.event_id
        existing = self.read("escalation_lifecycle") or {"escalations": []}
        escs_data = existing.get("escalations", [])
        escs: list[dict[str, object]] = list(escs_data) if isinstance(escs_data, list) else []
        node = next((e for e in escs if e.get("escalation_id") == escalation_event_id), None)
        if node is None:
            node = {
                "escalation_id": escalation_event_id,
                "lifecycle_state": EscalationLifecycleState.RAISED.value,
                "raised_at": ev.timestamp.isoformat(),
                "raised_by_skill": self._RAISED_BY_SKILL_PLACEHOLDER,
                "escalation_kind": self._ESCALATION_KIND_PLACEHOLDER,
            }
            escs.append(node)
        node["lifecycle_state"] = EscalationLifecycleState.RAISED.value
        raised_at = src.get("raised_at")
        node["raised_at"] = (
            raised_at if isinstance(raised_at, str) and raised_at else ev.timestamp.isoformat()
        )
        # raised_by_skill: World-B escalation 由 orchestrator/daemon 自 emit (provenance.skill_id 多为空),
        # 退记 actor_id (谁升的) — 非空且诚实, 不伪造一个 skill。
        skill_id = ev.provenance.skill_id
        actor_id = ev.provenance.actor_id
        node["raised_by_skill"] = (
            skill_id
            if (skill_id and skill_id.strip())
            else (actor_id if (actor_id and actor_id.strip()) else self._RAISED_BY_SKILL_PLACEHOLDER)
        )
        node["escalation_kind"] = self._world_b_escalation_kind(src)
        related_task = self._world_b_related_task(src)
        if related_task:
            node["related_task_ids"] = [related_task]
        related_finding = self._world_b_related_finding(src)
        if related_finding:
            node["related_finding_ids"] = [related_finding]
        EscalationLifecycleEntry.model_validate(node, strict=False)
        self.write_atomic("escalation_lifecycle", {"escalations": escs})

    def _apply_world_b_escalation_answer(
        self, ev: EventRecord, src: dict[str, object],
    ) -> None:
        """M23-F2: owner NJ (responds_to_escalation_event_id) 把对应 World-B escalation 闭环 resolve。

        匹配契约 = responds_to_escalation_event_id == GoalEscalationRaised 的 event_id (= 物化时的
        escalation_id 键)。只 update 已物化的 World-B 条目, 不为悬空 NJ 造幽灵条目 (native World-A
        escalation 由 EscalationResolved 自己闭环, 不走这条 NJ 路径)。
        """
        responds_to = src.get("responds_to_escalation_event_id")
        if not isinstance(responds_to, str) or not responds_to:
            return
        existing = self.read("escalation_lifecycle") or {"escalations": []}
        escs_data = existing.get("escalations", [])
        escs: list[dict[str, object]] = list(escs_data) if isinstance(escs_data, list) else []
        node = next((e for e in escs if e.get("escalation_id") == responds_to), None)
        if node is None:
            return  # NJ 指向的不是已物化的 World-B escalation — 不造幽灵 (诚实, 不伪造闭环)
        node["lifecycle_state"] = EscalationLifecycleState.RESOLVED.value
        node["resolved_at"] = ev.timestamp.isoformat()
        node["nature_judgment_event_id"] = ev.event_id
        EscalationLifecycleEntry.model_validate(node, strict=False)
        self.write_atomic("escalation_lifecycle", {"escalations": escs})

    # ─── node graphs (session/lock/escalation) 二阶 re-fold projection — K2b-REG §TG.4 ──

    @staticmethod
    def _node_event_to_flat(ev: EventRecord) -> dict[str, object]:
        """把 after_state-wrapped canonical EventRecord 翻成 node_reducers.reduce_node_events 期望的
        flat dict (adapter seam)。reduce_node_events 是纯函数, 只认顶层键; 这里是唯一阻抗匹配处。

        只对 _NODE_GRAPH_EVENTS 里的事件调 (当前 = SessionSpawned / LockAcquired, 二者 after_state 字段
        都是 reducer 键的超集, 直接摊平即可)。LockReleased / Escalation* 刻意不消费 (见 _NODE_GRAPH_EVENTS
        注: release 无 lock_id 缺口 + escalation 须 World-B 源), 故这里不需要也不该处理它们的特殊形态。
        """
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        flat: dict[str, object] = {
            "type": ev.event_type.value,
            "event_id": ev.event_id,
        }
        # 把 after_state 顶层字段摊平 (reducer 只读它认识的键; 多余键无害)。
        for k, v in after.items():
            flat.setdefault(k, v)
        return flat

    def _reduce_node_graphs(self, only: str | None = None) -> None:
        """K2b-REG (50-graph-protocol §TG.4) — session/lock/escalation 三图全量 re-fold (recompute-style)。

        ★ 为何全量重折而非 per-event upsert: reduce_node_events 是 whole-history fold — LockReleased
        失活的 held-by 边必须与对应 LockAcquired 在同一次 fold 的 accumulator 里才能匹配; 单事件折叠会
        让 lg["edges"] 为空 → release 静默 no-op (held-by 边永远 is_active=True = 该项目要治的假完成)。
        且 _edge 是 .append 非幂等, per-event merge 会重复加边。从空 fold 全量集两病皆免, 且让
        node_reducers 在生产真 load-bearing。同 impact_graph 二阶 recompute 范式。

        ``only``: None (catchup/_apply) → 写全部三图。指定单图名 (recompute_one) → 只写那一图,
        不碰邻居 (满足 recompute_one 的"不碰邻居"合约 — 三图同源一次折但单图修复只落自己那份)。

        当前消费集 = SessionSpawned / LockAcquired (见 _NODE_GRAPH_EVENTS)。故:
          - session_graph: 真物化 (血缘 spawned 边); 真兑现依赖 part 3 spawn 收口 emit SessionSpawned。
          - lock_graph: 只 acquire 侧 (held-by 边); release 缺口未接 → 边不失活 (交回主 session)。
          - escalation_graph: **空占位** (slot 在但当前无源喂进)。§6.3 明令 escalation 节点须源自
            World-B (GoalEscalationRaised + NatureJudgmentCaptured); 设计态 EscalationRaised/Resolved
            (生产 ≈0) 不该当源 (= "看着上图其实空/错源" 假完成)。真兑现待主 session 定 World-B source +
            solution-4 喂通 escalation_lifecycle 的依赖序。本步**不**消费 escalation 事件, 故此图诚实为空。
        """
        log = self._reduce_event_log
        if log is None:  # 无日志句柄 (理论不该到, catchup/recompute 都先 set) — 安全空写
            return
        node_events: list[EventRecord] = []
        for et in _NODE_GRAPH_EVENTS:
            node_events.extend(log.get_events_by_type(et))
        node_events.sort(key=lambda r: r.sequence_number)
        flat = [self._node_event_to_flat(ev) for ev in node_events]
        graphs = reduce_node_events(flat)
        # 三图同源一次重折; only=None 写全部, 否则只写指定一图 (recompute_one 不碰邻居)。
        for name in ("session_graph", "lock_graph", "escalation_graph"):
            if only is None or name == only:
                self.write_atomic(name, graphs[name])

    # ─── impact_graph 二阶 projection + get_impact_slice (M-0.2 §3.7/§7.6/§4.1.6) ──

    def _recompute_impact_graph(self) -> None:
        """M-0.2 §3.7/§7.6 — impact_graph 是二阶 projection: 不直接消费 event, 从已物化的
        concept_graph.edges + task_graph.edges 派生统一有向影响边集 (source→target)。

        每次 concept_graph 或 task_graph 更新后由 _apply 触发重算 (§7.6: 影响关系就是"沿概念图
        边和任务图边走")。is_active=false 的概念边 (removed) 排除 (§3.1 标 false 不物删, 不参与
        影响走)。task 依赖边无 is_active 概念 (reducer 加边即活)。

        重算从 ground-truth 一阶 projection 派生 → 幂等 (同 concept/task 图 → 同 impact 边集)。

        ─── 方向归一化 (统一 source=上游产出 / target=下游消费, 对齐任务边 + O-11 forward 语义) ───
        impact_graph 是统一有向影响边集, 其唯一方向约定 = forward (沿 source→target 出边走) 收集
        "下游受影响 entity"(O-11 §4 表: forward = 消费方发现 = "谁依赖 source_concept",
        M-2.1 spec L229; backward = 上游前提 = "我依赖谁", L230)。任务边的原始约定就是
        source=上游产出→target=下游消费 (dependency-policy: `B depends_on A` ⇒ add_edge(A→B),
        source=A=上游), 已对齐, 原样拷。

        但概念边的原始 source/target 约定与任务边 *相反*:
          - dependency (M-0.2 §3.2 depends_on): source=依赖方=下游, target=被依赖方=上游。
            (实证: 真图 `audit-fork-concept@v1 -> review-audit-three-layer-target@v1` =
             audit-fork 依赖/建立在 three-layer-target 之上, source=下游依赖方。)
          - reference (引用方→被引用): source=引用方=下游, target=被引用方=上游。
            (实证: 真图 `test-smoke-concept@v1 -> P-11` = test-smoke 引用 P-11, source=下游引用方。)
        故这两类 *归一化方向* (emit source=原 target=上游, target=原 source=下游) 才能让
        forward(source→target) 走到下游依赖方 (= O-11 "谁依赖我")。不归一会让 forward 在概念边上
        走到上游 (与任务边方向打架, CIA forward/backward 在概念边上跟设计反 — 本修根因)。

        逐类归一化动作 (只翻 dependency + reference, 别 blanket-flip):
          - dependency / reference → 翻 (source/target 互换), 见上。
          - contract → 不翻: contract 边方向无关, CIA contract_check 双向遍历 (cia_query 沿
            forward+backward 两个邻接表), 翻不翻结果同。原样拷保持 edge_id/路径稳定。
          - containment / data_schema → 不翻 (方向 *未归一化*): 它们的 impact 方向 M-0.2 §3.2
            欠定 (data_schema 根本不在 §3.2 6-type impact enum; containment=parent_of 父子的
            impact 方向双向/不明), 且不在任何 caller 的默认切片里 (forward/backward 默认 edge_types
            = {dependency, reference}; cascade 触发 = {dependency, reference, contract};
            data_schema_check 单独沿 data_schema 边走但语义是"schema 演化路径"非依赖方向)。
            归一前需先在 spec 定清 impact 方向 — 见 backlog, 不在此猜。
        """
        # 概念边里需要方向归一化 (翻 source/target) 的 edge_type — 见 docstring。
        # 模块级单一真源, cross_projection_consistency checker 镜像同一常量 (防 reducer↔checker 漂移)。
        _normalize_flip_concept_edge_types = IMPACT_GRAPH_NORMALIZE_FLIP_CONCEPT_EDGE_TYPES
        edges: list[dict[str, object]] = []
        cg = self.read("concept_graph") or {}
        for e in _as_list(cg.get("edges")):
            if e.get("is_active") is False:
                continue
            src = e.get("source_concept_id")
            tgt = e.get("target_concept_id")
            if isinstance(src, str) and isinstance(tgt, str) and src and tgt:
                etype = e.get("edge_type")
                # dependency/reference 原始约定 source=下游 → 翻成 source=上游/target=下游, 对齐
                # 任务边 + forward 语义 (docstring)。contract / containment / data_schema 原样拷。
                if etype in _normalize_flip_concept_edge_types:
                    out_src, out_tgt = tgt, src
                else:
                    out_src, out_tgt = src, tgt
                # edge_id carried so CIA query (M-2.1 §4.2 SliceResult.path) can name the
                # concept_graph edge it walked (additive — get_impact_slice reads only source/target).
                edges.append(
                    {
                        "source": out_src,
                        "target": out_tgt,
                        "kind": "concept",
                        "edge_type": etype,
                        "edge_id": e.get("edge_id"),
                    },
                )
        tg = self.read("task_graph") or {}
        for e in _as_list(tg.get("edges")):
            src = e.get("source_task_id")
            tgt = e.get("target_task_id")
            if isinstance(src, str) and isinstance(tgt, str) and src and tgt:
                # task edges have no edge_id in task_graph — synthesize a stable ref so path is
                # never empty when a CIA slice crosses task space (concept-kind CIA queries ignore these).
                edges.append(
                    {
                        "source": src,
                        "target": tgt,
                        "kind": "task",
                        "edge_type": e.get("dependency_type"),
                        "edge_id": f"task:{src}->{tgt}",
                    },
                )
        self.write_atomic("impact_graph", {"edges": edges})

    def get_impact_slice(
        self,
        entity_id: str,
        *,
        direction: str,
        depth: int,
        event_log: EventLog | None = None,
    ) -> dict[str, list[str]]:
        """M-0.2 §4.1.6 — get_impact_slice(entity_id, direction[forward|backward], depth) →
        {affected: []}. O-11 变更影响识别核心能力, 在 impact_graph projection 上 BFS"沿边走"。

        direction=forward:  改了 entity_id, 沿出边 (source==entity) 走 depth 跳, 收集下游受影响节点。
        direction=backward: entity_id 依赖谁, 沿入边 (target==entity) 走 depth 跳, 收集上游依赖节点。
        depth 截断 BFS 最大跳数。entity_id 本身不计入 affected; 未知 entity → 空切片 (不抛)。

        §4.2 read-triggers-catch-up: when `event_log` is given, catches up first so the slice
        reflects the latest committed-visible impact_graph. Omitting it reads the current
        materialized state (back-compat with callers that catch up upstream).
        """
        if direction not in ("forward", "backward"):
            msg = f"direction 必须是 forward|backward, 收到 {direction!r}"
            raise ValueError(msg)
        if event_log is not None:
            self.catchup(event_log)  # §4.2 read-triggers-catch-up
        ig = self.read("impact_graph") or {"edges": []}
        edges = _as_list(ig.get("edges"))
        # 邻接表: forward 用 source→[target]; backward 用 target→[source]。
        adj: dict[str, list[str]] = {}
        for e in edges:
            src = e.get("source")
            tgt = e.get("target")
            if not (isinstance(src, str) and isinstance(tgt, str)):
                continue
            if direction == "forward":
                adj.setdefault(src, []).append(tgt)
            else:
                adj.setdefault(tgt, []).append(src)
        affected: list[str] = []
        seen: set[str] = {entity_id}
        frontier = [entity_id]
        for _ in range(max(0, depth)):
            next_frontier: list[str] = []
            for node in frontier:
                for nxt in adj.get(node, []):
                    if nxt not in seen:
                        seen.add(nxt)
                        affected.append(nxt)
                        next_frontier.append(nxt)
            if not next_frontier:
                break
            frontier = next_frontier
        return {"affected": affected}

    # ─── concept_evolution read API (M-0.2 §3.7 / RUN-038 L0增强) ─────────────

    def get_concept_evolution(self, concept_id: str) -> dict[str, object]:
        """M-0.2 §3.7 — concept_evolution 概念演化链。从 supersede chain 推导, 沿
        concept_graph.supersede_index 追溯。§3.7 明令"不需要独立 state 文件" → 这是
        concept_graph.supersede_index 的 read API 封装 (非物化 projection)。

        返回 {concept_id, supersede_events: [event_id], node: dict|None}:
          supersede_events = supersede_index[concept_id] (哪些 ConceptGraphProposal supersede 过它,
            按物化顺序); node = concept_graph.nodes 里该概念当前态 (已 apply proposal); 未知概念 →
            空链 + node=None (不抛)。
        """
        cg = self.read("concept_graph") or {}
        supersede_index_raw = cg.get("supersede_index")
        supersede_events: list[str] = []
        if isinstance(supersede_index_raw, dict):
            entry = supersede_index_raw.get(concept_id)
            if isinstance(entry, list):
                supersede_events = [str(x) for x in entry]
        node = next(
            (n for n in _as_list(cg.get("nodes")) if n.get("concept_id") == concept_id),
            None,
        )
        return {"concept_id": concept_id, "supersede_events": supersede_events, "node": node}

    # ─── decision projection (M-0.2 附录B Patch 6 / RUN-038 L0增强) ───────────

    def _reduce_decision(self, ev: EventRecord) -> None:
        """M-0.2 附录B Patch 6 — decision projection. 从具体 semantic_judgment 类 event 推导
        (Patch 6: 原文引用的统一 SemanticJudgmentMade event 不存在, 改按 event_category 门收所有
        具体类型 ResolverDecisionMade / ObligationScopeJudged / RiskSurfaceAssigned / 等)。

        每条 judgment = 一个 semantic_judgment event 记录, upsert by judgment_event_id:
          judgment_event_id / judgment_type (具体 event_type) / subject_ids (从 subjects[] 提取) /
          current_validity / superseded_by_event_id? / evidence_summary / uncertainty?。

        current_validity 推导 (Patch 6 — "记录不可变但判断可被修正" 对齐 M-0.1a Patch 5):
          默认 active; 本 event 若 supersede 指向某旧 judgment → 旧标 superseded + superseded_by。
        canonical supersede 链 (EventBase.supersede.superseded_event_id) 是可查事实源 → 真物化。

        诚实边界 (dependency_blocked, 不伪造): challenged / retracted 当前 canonical schema 无可用
        产生引用 —— FindingDisputed (M-0.1 §3.8) 只有 finding_id (dispute finding 非 judgment),
        enum 无 retraction event_type。故只真物化 active / superseded; challenged / retracted 留债
        (依赖 schema 补 FindingDisputed→judgment 引用 / 新 retraction event_type)。

        Idempotent: 同 judgment_event_id 在位 upsert; supersede 标记幂等 (重放结果一致)。
        """
        existing = self.read("decision") or {"judgments": []}
        judgments_data = existing.get("judgments", [])
        judgments: list[dict[str, object]] = (
            list(judgments_data) if isinstance(judgments_data, list) else []
        )
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}
        subject_ids = [
            {"entity_type": str(s.entity_type.value), "entity_id": s.entity_id} for s in ev.subjects
        ]
        evidence_summary = after.get("evidence_summary")
        if not isinstance(evidence_summary, str):
            flat_ev = payload.get("evidence_summary")
            evidence_summary = flat_ev if isinstance(flat_ev, str) else ""
        uncertainty = payload.get("uncertainty")
        if uncertainty is None:
            uncertainty = after.get("uncertainty")

        record: dict[str, object] = {
            "judgment_event_id": ev.event_id,
            "judgment_type": ev.event_type.value,
            "subject_ids": subject_ids,
            "current_validity": "active",
            "superseded_by_event_id": None,
            "evidence_summary": evidence_summary,
            "uncertainty": uncertainty if isinstance(uncertainty, str) else None,
        }
        node = next((j for j in judgments if j.get("judgment_event_id") == ev.event_id), None)
        if node is None:
            judgments.append(record)
        else:
            # upsert: 保留已推导的 current_validity/superseded_by (旧 judgment 可能已被后续 supersede)。
            preserved_validity = node.get("current_validity", "active")
            preserved_by = node.get("superseded_by_event_id")
            node.update(record)
            node["current_validity"] = preserved_validity
            node["superseded_by_event_id"] = preserved_by

        # supersede 推导: 本 event 若指向某旧 judgment → 旧标 superseded + superseded_by 本 event。
        superseded_id = ev.supersede.superseded_event_id if ev.supersede.is_supersede else None
        if isinstance(superseded_id, str) and superseded_id:
            for j in judgments:
                if j.get("judgment_event_id") == superseded_id:
                    j["current_validity"] = "superseded"
                    j["superseded_by_event_id"] = ev.event_id
        self.write_atomic("decision", {"judgments": judgments})

    # ─── review_plan reducer (M-1.5 §3.1 / RUN-035 T-L1-50) ──────────────────

    def _reduce_review_plan(self, ev: EventRecord) -> None:
        """M-1.5 §3.1 review_plan projection (RUN-035 T-L1-50).

        ReviewPlanCreated → upsert review_plan node (review_plan_id keyed). supersede (v2)
        标 superseded 旧 plan + 新 plan active (M-1.5 §3.1 Patch d, M-0.2 reducer 更新)。
        让 design-time mode 产的 ReviewPlanCreated 真被消费 (anti-trap: emit ≠ 被读)。
        Idempotent: 同 review_plan_id 在位 upsert。
        """
        existing = self.read("review_plan") or {"review_plans": []}
        plans_data = existing.get("review_plans", [])
        plans: list[dict[str, object]] = list(plans_data) if isinstance(plans_data, list) else []
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        src = after if isinstance(after, dict) else payload
        review_plan_id = src.get("review_plan_id")
        if not isinstance(review_plan_id, str) or not review_plan_id:
            return
        # supersede: v2 标记被它取代的 v1 plan canonical_state=superseded。
        # superseded_event_id 在 EventBase.supersede (顶层属性), 非 payload。
        superseded_id = getattr(getattr(ev, "supersede", None), "superseded_event_id", None)
        if isinstance(superseded_id, str) and superseded_id:
            for p in plans:
                if p.get("created_by_event_id") == superseded_id:
                    p["canonical_state"] = "superseded"
                    p["superseded_by"] = ev.event_id
        node = next((p for p in plans if p.get("review_plan_id") == review_plan_id), None)
        record = {
            "review_plan_id": review_plan_id,
            "associated_consensus_id": src.get("associated_consensus_id"),
            "associated_brief_id": src.get("associated_brief_id"),
            "dimensions": src.get("dimensions", []),
            "voi_criteria": src.get("voi_criteria", []),
            "historical_failures_feed": src.get("historical_failures_feed", []),
            "batch_info": src.get("batch_info"),
            "canonical_state": "active",
            "superseded_by": None,
            "created_by_event_id": ev.event_id,
        }
        if node is None:
            plans.append(record)
        else:
            node.update(record)
        self.write_atomic("review_plan", {"review_plans": plans})

    # ─── task_graph reducer (M-0.2 §3.2 / T-L0-06) ──────────────────────────

    def _reduce_task_graph(self, ev: EventRecord) -> None:
        """M-0.2 §3.2 task_graph reducer (was a stub — RUN-031 T-L0-06).

        TaskNodeCreated → nodes+1 (status=created); TaskDependencyEdgeAdded → edges+1;
        TaskReadSetClaimed/WriteSetClaimed → node read_set/write_set; TaskModelTierAssigned →
        model_tier; TaskPackagePublished → package_hash/is_self_contained + status=planned;
        TaskRunCompleted → last_run_outcome + status. Dedup by id / overwrite-in-place keeps a
        full-log rebuild idempotent (§6 — identical state_hash on re-replay).
        """
        existing = self.read("task_graph") or {}
        nodes = _as_list(existing.get("nodes"))
        edges = _as_list(existing.get("edges"))
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}

        if ev.event_type is EventType.TASK_NODE_CREATED:
            task_id = after.get("task_id")
            if isinstance(task_id, str) and not any(n.get("task_id") == task_id for n in nodes):
                nodes.append(
                    {
                        "task_id": task_id,
                        "task_type": after.get("task_type", ""),
                        # T-LND-09 (INV-B2-4): 暴露 phase (design|implementation|review) 让阶段
                        # 先后可机器校验 (T-LND-10 据此判 review_plan 先于 implementation)。
                        "phase": after.get("phase"),
                        "parent_task_id": after.get("parent_task_id"),
                        "plan_id": after.get("plan_id"),
                        "description": after.get("description", ""),
                        "target_artifacts": after.get("target_artifacts", []),
                        "status": "created",
                        "model_tier": None,
                        "read_set": [],
                        "write_set": [],
                        "package_hash": None,
                        "is_self_contained": None,
                        "last_run_outcome": None,
                        "created_at_event_id": ev.event_id,
                        # fnd-r01-9 (owner-gate dispatch guard): 透传红线门标记 (缺 = False = 普通任务)。
                        "requires_owner_gate": after.get("requires_owner_gate", False) is True,
                        "owner_gate_reason": (
                            after.get("owner_gate_reason")
                            if isinstance(after.get("owner_gate_reason"), str)
                            else None
                        ),
                    },
                )
        elif ev.event_type is EventType.TASK_DEPENDENCY_EDGE_ADDED:
            # T-FIX-B6-01: 边带 is_active (ADDED 时 True, REMOVED 时翻 False)。get_neighborhood
            # 跳 is_active=False 的边 (与 concept_graph 边、AtReference 同范式)。re-add 一条曾被撤的
            # 边 → 复活 (is_active 翻回 True), 而非追加重复 dict。
            ident = (
                after.get("source_task_id", ""),
                after.get("target_task_id", ""),
                after.get("dependency_type", ""),
            )
            existing_edge = next(
                (
                    e
                    for e in edges
                    if (
                        e.get("source_task_id", ""),
                        e.get("target_task_id", ""),
                        e.get("dependency_type", ""),
                    )
                    == ident
                ),
                None,
            )
            if existing_edge is None:
                edges.append(
                    {
                        "source_task_id": ident[0],
                        "target_task_id": ident[1],
                        "dependency_type": ident[2],
                        "is_active": True,
                    },
                )
            else:
                existing_edge["is_active"] = True
        elif ev.event_type is EventType.TASK_DEPENDENCY_EDGE_REMOVED:
            # T-FIX-B6-01 (PLAN-seam#1): 撤一条错向/假依赖边。append-only 真相源不物理删 ADDED
            # 事件; reducer 把对应 edge dict 标 is_active=False。撤边身份用 before_state 的
            # (source,target,dependency_type) 三元组定位 (task 边无 edge_id)。
            before = payload.get("before_state")
            before = before if isinstance(before, dict) else {}
            ident = (
                before.get("source_task_id", ""),
                before.get("target_task_id", ""),
                before.get("dependency_type", ""),
            )
            for e in edges:
                if (
                    e.get("source_task_id", ""),
                    e.get("target_task_id", ""),
                    e.get("dependency_type", ""),
                ) == ident:
                    e["is_active"] = False
        elif ev.event_type in {
            EventType.TASK_READ_SET_CLAIMED,
            EventType.TASK_WRITE_SET_CLAIMED,
            EventType.TASK_MODEL_TIER_ASSIGNED,
            EventType.TASK_PACKAGE_PUBLISHED,
            EventType.TASK_RUN_COMPLETED,
            EventType.TASK_NODE_CLOSED,  # T-DEC-1 — done-elsewhere closure → status=closed 终态
            # owner-gate-clearance@v1 — owner 显式解除 owner-gate → node.requires_owner_gate 翻 False
            EventType.TASK_NODE_OWNER_GATE_CLEARED,
        }:
            self._update_task_node(ev, after, nodes)

        self.write_atomic("task_graph", {"nodes": nodes, "edges": edges})

    @staticmethod
    def _update_task_node(
        ev: EventRecord,
        after: dict[str, object],
        nodes: list[dict[str, object]],
    ) -> None:
        """Apply a per-task update event to its node in place (skip if node not yet created)."""
        task_id = after.get("task_id")
        node = next((n for n in nodes if n.get("task_id") == task_id), None)
        if node is None:
            return
        if ev.event_type is EventType.TASK_READ_SET_CLAIMED:
            node["read_set"] = after.get("read_set", [])
        elif ev.event_type is EventType.TASK_WRITE_SET_CLAIMED:
            node["write_set"] = after.get("write_set", [])
        elif ev.event_type is EventType.TASK_MODEL_TIER_ASSIGNED:
            node["model_tier"] = after.get("model_tier")
        elif ev.event_type is EventType.TASK_PACKAGE_PUBLISHED:
            node["package_hash"] = after.get("package_hash")
            node["is_self_contained"] = after.get("is_self_contained")
            node["status"] = "planned"
        elif ev.event_type is EventType.TASK_RUN_COMPLETED:
            outcome = after.get("outcome")
            node["last_run_outcome"] = outcome
            node["status"] = _TASK_RUN_STATUS.get(outcome if isinstance(outcome, str) else "", "in_progress")
        elif ev.event_type is EventType.TASK_NODE_CLOSED:
            # done-elsewhere-task-closure@v1 (T-DEC-1): 一等终态 closed —— 任务其实已在别处做完,
            # 诚实终态化, 永不重派 (区别 abort)。只翻 status (TaskNode 是 _STRICT/extra-forbid, 不加
            # 新字段); closed_task_ids 由 readyset-closure-exclusion-contract@v1 (T-DEC-3) 从
            # TaskNodeClosed 事件派生, 不靠 node 上的额外字段。last_run_outcome 留原值 (closure 不是
            # run outcome)。closed 是终态, 优先于运行态。
            node["status"] = "closed"
        elif ev.event_type is EventType.TASK_NODE_OWNER_GATE_CLEARED:
            # owner-gate-clearance@v1 (autopilot-owner-presence-removal): owner 经合法机制 (CLI +
            # commit gate) 显式解除本 task 的 owner-gate。可见态与派发行为 (_task_owner_gate 认已解)
            # 一致 —— node.requires_owner_gate 翻 False + 清 reason。canonical TaskNodeCreated 原标记
            # 不物理改 (append-only 真相源), 本处只是投影的叠加解除。
            node["requires_owner_gate"] = False
            node["owner_gate_reason"] = None

    # ─── ownership reducer (M-0.2 §3.4 + Patch 8 zombie-lock handoff / T-L0-07) ─

    def _reduce_ownership(self, ev: EventRecord) -> None:
        """M-0.2 §3.4 ownership reducer (was a stub — RUN-031 T-L0-07).

        TaskWriteSetClaimed → each write_set entity locked to (owner_task_id, lock_holder_run_id);
        CommitAccepted → resolve envelope_event_id → envelope.write_set, release those locks;
        LockReleased (Patch 8 zombie-lock handoff, M-0.5 §7.4) → release the listed entities.
        This is the M-0.5 §9.2 正规输入 the WriteConflict gate reads. Idempotent on re-replay.
        """
        existing = self.read("ownership") or {}
        entities = _as_list(existing.get("entities"))
        payload = ev.payload if isinstance(ev.payload, dict) else {}

        if ev.event_type is EventType.TASK_WRITE_SET_CLAIMED:
            after = payload.get("after_state")
            after = after if isinstance(after, dict) else {}
            task_id = after.get("task_id")
            run_id = ev.provenance.run_id
            for w in after.get("write_set", []) if isinstance(after.get("write_set"), list) else []:
                if not isinstance(w, dict):
                    continue
                self._set_lock(entities, w.get("entity_type"), w.get("entity_id"), owner_task_id=task_id, run_id=run_id)
        elif ev.event_type is EventType.COMMIT_ACCEPTED:
            self._release_committed_locks(entities, payload)
        elif ev.event_type is EventType.LOCK_RELEASED:
            after = payload.get("after_state")
            after = after if isinstance(after, dict) else {}
            for e in after.get("entities", []) if isinstance(after.get("entities"), list) else []:
                if isinstance(e, dict):
                    self._release_lock(entities, e.get("entity_type"), e.get("entity_id"))

        self.write_atomic("ownership", {"entities": entities})

    @staticmethod
    def _set_lock(
        entities: list[dict[str, object]],
        entity_type: object,
        entity_id: object,
        *,
        owner_task_id: object,
        run_id: object,
    ) -> None:
        if not isinstance(entity_type, str) or not isinstance(entity_id, str):
            return
        node = next(
            (e for e in entities if e.get("entity_type") == entity_type and e.get("entity_id") == entity_id),
            None,
        )
        update = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "owner_task_id": owner_task_id,
            "write_locked": True,
            "lock_holder_run_id": run_id,
        }
        if node is None:
            entities.append(update)
        else:
            node.update(update)

    @staticmethod
    def _release_lock(entities: list[dict[str, object]], entity_type: object, entity_id: object) -> None:
        for e in entities:
            if e.get("entity_type") == entity_type and e.get("entity_id") == entity_id:
                e["write_locked"] = False
                e["lock_holder_run_id"] = None

    def _release_committed_locks(self, entities: list[dict[str, object]], payload: dict[str, object]) -> None:
        """CommitAccepted → resolve the path-B envelope and release its write_set locks (§3.4)."""
        env_id = payload.get("envelope_event_id")
        if not isinstance(env_id, str) or self._reduce_event_log is None:
            return
        env = self._reduce_event_log.get_event(env_id)
        if env is None:
            return
        write_set = env.payload.get("write_set") if isinstance(env.payload, dict) else None
        if not isinstance(write_set, list):
            return
        for w in write_set:
            if isinstance(w, dict):
                self._release_lock(entities, w.get("entity_type"), w.get("entity_id"))

    # ─── at_reference_graph reducer (M-0.2 §3.5 / T-L0-08) ───────────────────

    def _reduce_at_reference_graph(self, ev: EventRecord) -> None:
        """M-0.2 §3.5 at_reference_graph reducer (was a stub — RUN-031 T-L0-08).

        AtReferenceAdded → references+1 (is_active=true); AtReferenceRemoved → matching
        reference is_active=false. Maps the producer's source_entity/target_entity {type,id,
        field_path} onto the projection's {entity_type,entity_id,field_path} endpoint shape.

        T-L1-14 (波3): also materialize the M-1.2 typed @ locator CLI shape (after_state with
        at_reference / target_concept_name / lock_event_id / on_target_supersede_policy) — the
        consensus CLI emits this richer shape, not the T-L0-08 source_entity/target_entity one,
        so without this branch every CLI-created @ reference never entered the projection and
        §4.6 detect_stale_references (which queries this projection) had nothing to scan.
        The §4.3 auxiliary events advance the locked event / flag staleness:
          AtReferenceAutoUpdated      → locked_event_id := new_lock (脱 stale, 跟随最新);
          AtReferenceTargetSuperseded → target_superseded_at := new_target_event +
                                         pending_status (pinned_to_old / awaiting_decision).
        Dedup by reference_id (or at_reference key) keeps re-replay idempotent (§6).
        """
        existing = self.read("at_reference_graph") or {}
        refs = _as_list(existing.get("references"))
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}

        if ev.event_type is EventType.AT_REFERENCE_ADDED:
            ref_id = after.get("reference_id")
            if isinstance(ref_id, str):
                # T-L0-08 source_entity/target_entity shape.
                if not any(r.get("reference_id") == ref_id for r in refs):
                    refs.append(
                        {
                            "reference_id": ref_id,
                            "source": _endpoint(after.get("source_entity")),
                            "target": _endpoint(after.get("target_entity")),
                            "reference_type": after.get("reference_type", ""),
                            "is_active": True,
                            "created_at_event_id": ev.event_id,
                        },
                    )
            else:
                # T-L1-14: M-1.2 typed @ locator CLI shape (no reference_id; keyed by at_reference).
                at_ref = after.get("at_reference")
                if isinstance(at_ref, str) and not any(r.get("reference_id") == at_ref for r in refs):
                    refs.append(
                        {
                            "reference_id": at_ref,
                            "source_concept_id": after.get("source_concept_id", ""),
                            "target_concept_name": after.get("target_concept_name", ""),
                            "locator_type": after.get("locator_type", ""),
                            "field_path": after.get("field_path"),
                            "state_anchor": after.get("state_anchor"),
                            # locked_event_id is the @-suffix lock if present, else the add event.
                            "locked_event_id": after.get("lock_event_id") or ev.event_id,
                            "on_target_supersede_policy": after.get("on_target_supersede_policy"),
                            "is_active": True,
                            "is_stale": False,
                            "created_at_event_id": ev.event_id,
                        },
                    )
        elif ev.event_type is EventType.AT_REFERENCE_REMOVED:
            before = payload.get("before_state")
            before = before if isinstance(before, dict) else {}
            ref_id = after.get("reference_id") or before.get("reference_id")
            for r in refs:
                if r.get("reference_id") == ref_id:
                    r["is_active"] = False
        elif ev.event_type is EventType.AT_REFERENCE_AUTO_UPDATED:
            ref_id = after.get("reference_id")
            for r in refs:
                if r.get("reference_id") == ref_id:
                    # auto_accept_latest: 跟随到 supersede 后新 lock → 不再 stale。
                    r["locked_event_id"] = after.get("new_lock_event_id") or r.get("locked_event_id")
                    r["is_stale"] = False
        elif ev.event_type is EventType.AT_REFERENCE_TARGET_SUPERSEDED:
            ref_id = after.get("reference_id")
            for r in refs:
                if r.get("reference_id") == ref_id:
                    # pin_to_snapshot / explicit_decision_required: target 已 supersede, ref 未跟随。
                    r["is_stale"] = True
                    r["target_superseded_at_event_id"] = after.get("new_target_event_id")
                    r["staleness_status"] = after.get("status")
                    # aux event 携带 supersede 的完整 concept_id (含版本) — 比 add 时版本归一的
                    # target_concept_name 更精确, 用它覆盖 stale 报告的 target。
                    superseded_target = after.get("target_concept_name")
                    if isinstance(superseded_target, str) and superseded_target:
                        r["target_concept_name"] = superseded_target

        self.write_atomic("at_reference_graph", {"references": refs})

    # ─── concept_graph reducer (M-0.2 §3.1) ─────────────────────────────────

    def _reduce_concept_graph(self, ev: EventRecord) -> None:
        """M-0.2 §3.1 concept_graph reducer (F-019-8: was a stub).

        ConceptCreated → nodes 加一条; ConceptEdgeAdded → edges 加一条;
        ConceptEdgeRemoved → 对应 edge is_active=false (不物理删除);
        ConceptStateTransition → 对应 node saga_state 更新;
        ConceptGraphProposal (committed supersede) → 按 proposed_changes 逐条 apply
        + supersede_index 加记录. Dedup by id keeps re-apply idempotent (§6).
        """
        existing = self.read("concept_graph") or {}
        nodes = _as_list(existing.get("nodes"))
        edges = _as_list(existing.get("edges"))
        supersede_index_raw = existing.get("supersede_index")
        supersede_index: dict[str, list[str]] = {}
        if isinstance(supersede_index_raw, dict):
            for k, v in supersede_index_raw.items():
                if isinstance(k, str) and isinstance(v, list):
                    supersede_index[k] = [str(x) for x in v]
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        after = payload.get("after_state")
        after = after if isinstance(after, dict) else {}

        if ev.event_type is EventType.CONCEPT_CREATED:
            # T-L1-02 (RUN-032): ConceptCreated 有两 shape — M-0.1/M-1.2 nested (after_state)
            # 跟 M-1.1 §5.5 brief-seed flat (ConceptSeedCreatedPayload: concept_id/concept_name/
            # definition/seed_origin/source_brief_id, 无 after_state, Patch Q). 之前 reducer 只
            # 读 after_state.concept_id → flat 种子 concept_id=None 静默丢. 现两 shape 都读,
            # 并保 seed_origin / source_brief_id (done_criteria: seed_origin=true + source_brief_id 互链).
            src = after if after else payload  # flat 时 after={} → 读 payload 顶层
            concept_id = src.get("concept_id")
            if isinstance(concept_id, str) and not any(n.get("concept_id") == concept_id for n in nodes):
                # flat 用 concept_name, nested 用 name
                name = src.get("name", src.get("concept_name", ""))
                nodes.append(
                    {
                        "concept_id": concept_id,
                        "name": name,
                        "definition": src.get("definition", ""),
                        "saga_state": None,
                        "created_by_stage": src.get("created_by_stage", ""),
                        "created_at_event_id": ev.event_id,
                        "seed_origin": bool(src.get("seed_origin", False)),
                        "source_brief_id": src.get("source_brief_id"),
                    },
                )
        elif ev.event_type is EventType.CONCEPT_EDGE_ADDED:
            edge_id = after.get("edge_id")
            if isinstance(edge_id, str) and not any(e.get("edge_id") == edge_id for e in edges):
                edges.append(
                    {
                        "edge_id": edge_id,
                        "source_concept_id": after.get("source_concept_id", ""),
                        "target_concept_id": after.get("target_concept_id", ""),
                        "edge_type": after.get("edge_type", ""),
                        "direction": after.get("direction", ""),
                        "created_at_event_id": ev.event_id,
                        "is_active": True,
                    },
                )
        elif ev.event_type is EventType.CONCEPT_EDGE_REMOVED:
            before = payload.get("before_state")
            before = before if isinstance(before, dict) else {}
            edge_id = before.get("edge_id")
            for e in edges:
                if e.get("edge_id") == edge_id:
                    e["is_active"] = False
        elif ev.event_type is EventType.CONCEPT_STATE_TRANSITION:
            concept_id = after.get("concept_id")
            for n in nodes:
                if n.get("concept_id") == concept_id:
                    n["saga_state"] = after.get("saga_state")
        elif ev.event_type is EventType.CONCEPT_STATE_MACHINE_DEFINED:
            # T-L1-10 (波2): 物化校验后的状态机到对应 node.state_machine (此前 reducer 不
            # 认 ConceptStateMachineDefined → emit 了但 projection 永不入图 = 隐藏依赖断链).
            # CLI emit 路径已 fail-closed 校验 §6.1 6 条; 这里只负责物化 (last-writer-wins,
            # 同 concept 重定义覆盖 — supersede 走 ConceptGraphProposal). 若 node 还没建
            # (state-machine 先于 concept), 不创节点 (concept_id 来源是 ConceptCreated).
            concept_id = after.get("concept_id")
            sm = {
                "states": _as_list(after.get("states")),
                "transitions": _as_list(after.get("transitions")),
                "saga_pattern": bool(after.get("saga_pattern", True)),
                "defined_at_event_id": ev.event_id,
            }
            for n in nodes:
                if n.get("concept_id") == concept_id:
                    n["state_machine"] = sm
        elif ev.event_type is EventType.CONCEPT_GRAPH_PROPOSAL:
            self._apply_concept_graph_proposal(ev, after, nodes, supersede_index)

        self.write_atomic(
            "concept_graph",
            {"nodes": nodes, "edges": edges, "supersede_index": supersede_index},
        )

    @staticmethod
    def _apply_concept_graph_proposal(
        ev: EventRecord,
        after: dict[str, object],
        nodes: list[dict[str, object]],
        supersede_index: dict[str, list[str]],
    ) -> None:
        """ConceptGraphProposal: 按 proposed_changes 逐条 apply + supersede_index 加记录 (§3.1)."""
        changes = after.get("proposed_changes")
        if not isinstance(changes, list):
            return
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            entity_id = ch.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            recorded = supersede_index.setdefault(entity_id, [])
            if ev.event_id not in recorded:
                recorded.append(ev.event_id)
            new_value = ch.get("new_value")
            if isinstance(new_value, dict):
                for n in nodes:
                    if n.get("concept_id") == entity_id:
                        for field in ("name", "definition", "saga_state"):
                            if field in new_value:
                                n[field] = new_value[field]

    # ─── T-R01-7 concept_graph 共变回填 (派生, 非 canonical 写入) ──────────────────

    def concept_graph_coupling_backfill(self, event_log: EventLog) -> CouplingBackfillResult:
        """T-R01-7 (c-concept-graph-dag-multiparent-structure@v1): 对当前 concept_graph 跑
        共变回填, 返回回填边 + 前后连通度量 (孤立/连通块下降 = 本任务 done_criterion)。

        co-change 篮子源 = 概念图自身的演化史: 同一共识 brief 批次共同创建的概念 (ConceptCreated
        按 source_brief_event_id 分组) = 一个共变篮子; 并把该 brief 的概念节点 (planner-brief-
        <suffix>) 并入篮子, 让 brief 与其派生概念相连。这是 §3.7/§7.6 "二阶派生" 范式 (impact_graph
        从 concept_graph+task_graph 派生) 的同构 —— 共变回填边从历史**派生**, **不**伪造 canonical
        ConceptEdgeAdded (那会在 rebuild 时被清掉, 且谎称有人 author 过这条边)。order_key = 概念
        创建 seq (老在前 → 新概念 build-on 老基础)。

        【Scope 边界 @concept:concept-graph-self-heal-staged-not-live-wired@v1】
        这是「派生能力已交付」, 非「生产连续自愈」——两者显式分开。当前 src/ 零调用方;
        返回的 CouplingBackfillResult.new_edges 不写回 concept_graph.edges; 无任何
        投影/rebuild/仪表消费该派生结果。⟹ live 运行时图不随用自愈——「孤立单调下降/结构
        自愈」那组数字 (连通块 54→18 / 孤立 46→10) 是 commit 时经 tests/cia/test_t_r01_7.py
        一次性实测, 非 live 持续发生。生产接线是 owner-gated T-R01-9 的显式范围。
        前置硬门 INV-R017-BACKFILL-NO-PROD-WIRING-UNTIL-LIFT-FIXED:「派生结果在被任何生产
        消费方读取前 (当前 src/ 零调用方), 关联 finding f-r01-7-lift-correspondence-inert
        必须已 resolved; 当前无消费方⟹假阳性边危害仅潜在 (派生只读无消费); 一旦 T-R01-9
        接线生产消费, 假阳性边变真实图腐蚀, 故生产接线是 lift-fix 的下游。」
        """
        cg = self.read("concept_graph") or {}
        node_ids = {
            n.get("concept_id")
            for n in _as_list(cg.get("nodes"))
            if isinstance(n.get("concept_id"), str)
        }
        active_edges = [
            (e.get("source_concept_id"), e.get("target_concept_id"))
            for e in _as_list(cg.get("edges"))
            if e.get("is_active", True)
            and e.get("source_concept_id") in node_ids
            and e.get("target_concept_id") in node_ids
        ]
        baskets, seq_of = self._concept_coupling_baskets(event_log, node_ids)
        return derive_coupling_backfill(
            node_ids, active_edges, baskets, order_key=lambda n: seq_of.get(n, 0)
        )

    @staticmethod
    def _concept_coupling_baskets(
        event_log: EventLog,
        node_ids: set[str],
    ) -> tuple[list[frozenset[str]], dict[str, int]]:
        """从事件日志派生概念级共变篮子 + 概念创建序 (order_key 源)。

        每个共识 brief 批次 = 一个篮子: ConceptCreated 按 source_brief_event_id 分组的 concept_id
        集; 再经 InterviewBriefPublished 把 brief_id 映射回 planner-brief-<suffix> 概念节点并入
        篮子。只留两端皆为现存概念节点、size≥2 的篮子 (单概念批次不产共变对)。
        """
        batch: dict[str, set[str]] = defaultdict(set)
        seq_of: dict[str, int] = {}
        for ev in event_log.get_events_by_type(EventType.CONCEPT_CREATED):
            payload = ev.payload if isinstance(ev.payload, dict) else {}
            after = payload.get("after_state")
            src = after if isinstance(after, dict) and after else payload
            cid = src.get("concept_id")
            if not isinstance(cid, str) or cid not in node_ids:
                continue
            seq_of.setdefault(cid, ev.sequence_number)
            brief_eid = src.get("source_brief_event_id") or src.get("source_brief_id")
            if isinstance(brief_eid, str):
                batch[brief_eid].add(cid)
        brief_concept: dict[str, str] = {}
        for ev in event_log.get_events_by_type(EventType.INTERVIEW_BRIEF_PUBLISHED):
            payload = ev.payload if isinstance(ev.payload, dict) else {}
            bid = payload.get("brief_id") or payload.get("brief_event_id")
            if isinstance(bid, str):
                cand = "planner-brief-" + bid.replace("brief-", "")
                if cand in node_ids:
                    brief_concept[ev.event_id] = cand
                    seq_of.setdefault(cand, ev.sequence_number)
        baskets: list[frozenset[str]] = []
        for brief_eid, cids in batch.items():
            members = set(cids)
            bc = brief_concept.get(brief_eid)
            if bc:
                members.add(bc)
            if len(members) >= 2:
                baskets.append(frozenset(members))
        return baskets, seq_of

    # ─── M-1.1 §3.1/§10.1/§13.2 采访侧 3 projection (T-L1-06 / RUN-032) ────────────
    #  图 canonical 源 = event log (R4 A+). InformationNeedCreated/StatusChanged (Patch R
    #  flat payload, §5.2) → information_need_graph; 聚合 → interview_progress;
    #  InterviewBriefPublished → interview_brief_index. 三者皆 per-session keyed, 幂等
    #  (create dedup by need_id / status 末写 / 聚合从节点集重算 / brief 标志 set).

    def _reduce_information_need_graph(self, ev: EventRecord) -> None:
        """M-1.1 §3.1 information_need_graph reducer (per session).

        InformationNeedCreated → 加节点 (status 默认 pending, created_by_event_id 留痕);
        InformationNeedStatusChanged → 对应节点 status / last_status_change_event_id 更新
        + resolved 时填 nature_original_quote / meaning_interpretation. dedup by
        (session_id, need_id) 保 re-apply 幂等 (§6). edges 暂空 — §5.2 事件不携边,
        info_need 边需独立 event type (本批不扩, 留 §3.1 结构位).
        """
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        session_id = payload.get("session_id")
        need_id = payload.get("need_id")
        if not isinstance(session_id, str) or not isinstance(need_id, str):
            return
        existing = self.read("information_need_graph") or {}
        sessions_raw = existing.get("sessions")
        sessions: dict[str, dict[str, object]] = (
            {k: v for k, v in sessions_raw.items() if isinstance(v, dict)}
            if isinstance(sessions_raw, dict)
            else {}
        )
        sess = sessions.setdefault(session_id, {"session_id": session_id, "nodes": [], "edges": []})
        nodes = _as_list(sess.get("nodes"))

        if ev.event_type is EventType.INFORMATION_NEED_CREATED:
            if not any(n.get("need_id") == need_id for n in nodes):
                nodes.append(
                    {
                        "need_id": need_id,
                        "description": payload.get("description", ""),
                        "source": payload.get("source", ""),
                        "status": "pending",
                        "red_line": bool(payload.get("red_line", False)),
                        "red_line_reason": payload.get("red_line_reason"),
                        "nature_original_quote": None,
                        "meaning_interpretation": None,
                        "downstream_impact": payload.get("downstream_impact", []),
                        "dependencies": payload.get("dependencies", []),
                        "created_by_event_id": ev.event_id,
                        "last_status_change_event_id": None,
                    },
                )
        elif ev.event_type is EventType.INFORMATION_NEED_STATUS_CHANGED:
            new_status = payload.get("new_status")
            for n in nodes:
                if n.get("need_id") == need_id:
                    if isinstance(new_status, str):
                        n["status"] = new_status
                    n["last_status_change_event_id"] = ev.event_id
                    if payload.get("nature_original_quote") is not None:
                        n["nature_original_quote"] = payload.get("nature_original_quote")
                    if payload.get("meaning_interpretation") is not None:
                        n["meaning_interpretation"] = payload.get("meaning_interpretation")
                    break

        sess["nodes"] = nodes
        sessions[session_id] = sess
        self.write_atomic("information_need_graph", {"sessions": sessions})

    def _reduce_interview_progress(self, ev: EventRecord) -> None:
        """M-1.1 §10.1 interview_progress reducer (per-session 信息聚合).

        从 (本 _apply 中已更新的) information_need_graph 节点集**重算**聚合 (count by
        status / source / red_line) — 重算非增量, 故双 apply 幂等. InterviewBriefPublished
        → 标 brief_published + brief_event_id (set, 非 increment). schema 详设未给 (§10.1
        仅命名), 取最小可用 per-session 聚合.
        """
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        session_id = payload.get("session_id")
        if not isinstance(session_id, str):
            return
        existing = self.read("interview_progress") or {}
        sessions_raw = existing.get("sessions")
        sessions: dict[str, dict[str, object]] = (
            {k: v for k, v in sessions_raw.items() if isinstance(v, dict)}
            if isinstance(sessions_raw, dict)
            else {}
        )
        prev = sessions.get(session_id, {})
        prog: dict[str, object] = {"session_id": session_id}
        # carry forward brief 标志 (brief 事件跟 info-need 事件分别 apply)
        prog["brief_published"] = bool(prev.get("brief_published", False))
        prog["brief_event_id"] = prev.get("brief_event_id")

        # 从 information_need_graph 重算节点聚合
        ing = self.read("information_need_graph") or {}
        ing_sessions = ing.get("sessions")
        sess_nodes: list[dict[str, object]] = []
        if isinstance(ing_sessions, dict):
            sess_obj = ing_sessions.get(session_id)
            if isinstance(sess_obj, dict):
                sess_nodes = _as_list(sess_obj.get("nodes"))
        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        red_line_total = 0
        red_line_pending = 0
        for n in sess_nodes:
            status = str(n.get("status", ""))
            source = str(n.get("source", ""))
            by_status[status] = by_status.get(status, 0) + 1
            by_source[source] = by_source.get(source, 0) + 1
            if n.get("red_line"):
                red_line_total += 1
                if status == "pending":
                    red_line_pending += 1
        prog["total_needs"] = len(sess_nodes)
        prog["by_status"] = by_status
        prog["by_source"] = by_source
        prog["red_line_total"] = red_line_total
        prog["red_line_pending"] = red_line_pending

        if ev.event_type is EventType.INTERVIEW_BRIEF_PUBLISHED:
            prog["brief_published"] = True
            prog["brief_event_id"] = ev.event_id

        sessions[session_id] = prog
        self.write_atomic("interview_progress", {"sessions": sessions})

    def _reduce_interview_brief_index(self, ev: EventRecord) -> None:
        """M-1.1 §10.1 interview_brief_index reducer (brief lookup).

        InterviewBriefPublished → 按 brief_id 索引 (event_id / session_id /
        goal_completion_condition / concept_seeds_created / supersede 链). supersede 时
        回填被取代 brief 的 superseded_by. dedup by brief_id 保幂等. 只 index canonical
        InterviewBriefPublished (历史 stub-rewrap brief 是 Patch Q 前 legacy, 不在此重建).
        """
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        brief_id = payload.get("brief_id")
        if not isinstance(brief_id, str):
            return
        existing = self.read("interview_brief_index") or {}
        briefs_raw = existing.get("briefs")
        briefs: dict[str, dict[str, object]] = (
            {k: v for k, v in briefs_raw.items() if isinstance(v, dict)}
            if isinstance(briefs_raw, dict)
            else {}
        )
        briefs[brief_id] = {
            "brief_id": brief_id,
            "brief_event_id": ev.event_id,
            "session_id": payload.get("session_id"),
            "goal_completion_condition": payload.get("goal_completion_condition"),
            "concept_seeds_created": payload.get("concept_seeds_created", []),
            "supersedes": payload.get("supersedes"),
            "superseded_by": briefs.get(brief_id, {}).get("superseded_by"),
        }
        supersedes = payload.get("supersedes")
        if isinstance(supersedes, str):
            # 被取代的旧 brief 用 event_id 标识 (§4.4); 找它并回填 superseded_by.
            for old in briefs.values():
                if old.get("brief_event_id") == supersedes:
                    old["superseded_by"] = ev.event_id
        self.write_atomic("interview_brief_index", {"briefs": briefs})


_OBLIGATION_LIFECYCLE_EVENTS = frozenset(
    [
        EventType.OBLIGATION_CAPTURED,
        EventType.OBLIGATION_ACTIVATED,
        EventType.OBLIGATION_CHECKED,
        EventType.OBLIGATION_VIOLATED,
        EventType.OBLIGATION_EVOLVED,
        EventType.OBLIGATION_RETIRED,
    ],
)


_CONCEPT_GRAPH_EVENTS = frozenset(
    [
        EventType.CONCEPT_CREATED,
        EventType.CONCEPT_EDGE_ADDED,
        EventType.CONCEPT_EDGE_REMOVED,
        EventType.CONCEPT_STATE_TRANSITION,
        EventType.CONCEPT_STATE_MACHINE_DEFINED,
        EventType.CONCEPT_GRAPH_PROPOSAL,
    ],
)


_TASK_GRAPH_EVENTS = frozenset(
    [
        EventType.TASK_NODE_CREATED,
        EventType.TASK_DEPENDENCY_EDGE_ADDED,
        EventType.TASK_DEPENDENCY_EDGE_REMOVED,  # T-FIX-B6-01 — 撤边消费
        EventType.TASK_NODE_CLOSED,  # T-DEC-1 — done-elsewhere 关闭 → status=closed 终态
        # owner-gate-clearance@v1 — owner 显式解除 owner-gate → node.requires_owner_gate 翻 False
        EventType.TASK_NODE_OWNER_GATE_CLEARED,
        EventType.TASK_READ_SET_CLAIMED,
        EventType.TASK_WRITE_SET_CLAIMED,
        EventType.TASK_MODEL_TIER_ASSIGNED,
        EventType.TASK_PACKAGE_PUBLISHED,
        EventType.TASK_RUN_COMPLETED,
    ],
)


# Events that mutate ownership/lock state (M-0.2 §3.4 + Patch 8). TaskWriteSetClaimed also
# feeds task_graph; CommitAccepted also feeds commit_history — hence the additive dispatch.
_OWNERSHIP_EVENTS = frozenset(
    [
        EventType.TASK_WRITE_SET_CLAIMED,
        EventType.COMMIT_ACCEPTED,
        EventType.LOCK_RELEASED,
    ],
)


_AT_REFERENCE_EVENTS = frozenset(
    [
        EventType.AT_REFERENCE_ADDED,
        EventType.AT_REFERENCE_REMOVED,
        # T-L1-13/T-L1-14 (波3): 失效响应辅助 event 喂 at_reference_graph —
        # auto_accept_latest 推进 locked_event_id (脱 stale); target supersede 标 stale。
        EventType.AT_REFERENCE_AUTO_UPDATED,
        EventType.AT_REFERENCE_TARGET_SUPERSEDED,
    ],
)


# T-L1-06 (RUN-032) — information_need_graph / interview_progress 消费的采访侧事件 (Patch R).
_INFORMATION_NEED_EVENTS = frozenset(
    [
        EventType.INFORMATION_NEED_CREATED,
        EventType.INFORMATION_NEED_STATUS_CHANGED,
    ],
)


# RUN-035 0-E-4 — finding_lifecycle (M-0.2 §3.10) 三态生命周期 (T-L1-52/53; 与 T-L1-64 重叠,
# 见 FINDINGS). FindingCreated→created, FindingVerified→verified, FindingDisputed→disputed,
# FindingResolved→resolved。事件 emit ≠ 被消费 (anti-trap): 这个 reducer 让 review 三态
# 事件真归约进 projection, 否则 "事件发了没人读" 是静默假完整。
_FINDING_LIFECYCLE_EVENTS = frozenset(
    [
        EventType.FINDING_CREATED,
        EventType.FINDING_VERIFIED,
        EventType.FINDING_DISPUTED,
        EventType.FINDING_RESOLVED,
        EventType.FINDING_ACCEPTED,  # f-escalation-task-oriented — ACCEPTED 终态 (哨兵永不再报)
        # f-stale-closure-contract-permanently-unclosable — **非** state transition: amend 只换生效
        # 合约、不动 lifecycle_state。喂进本 reducer 是为了让"合约被修订过"这件事在 projection 上可见
        # (否则事件发了没人读 = 静默假完整), 不是为了改状态。
        EventType.FINDING_CLOSURE_CONTRACT_AMENDED,
    ],
)

# T-L0-11 (波1) — detection_rule_lifecycle reducer 喂入事件 (M-0.2 §3.11)。
_DETECTION_RULE_EVENTS = frozenset(
    [
        EventType.DETECTION_RULE_PROPOSED,
        EventType.DETECTION_RULE_SHADOWING,
        EventType.DETECTION_RULE_PROMOTED,
        EventType.DETECTION_RULE_RETIRED,
    ],
)

# M23-F2 / rc-unify-escalation-event-source-materialize-projection: World-B 升级源
# (GoalEscalationRaised + owner NJ answer) 的 (kind, 字段源 dict) 解包。这两族在生产里以
# stub-rewrap NodeTouched (payload.kind=<Type>, stub_original_payload={...}) 落账 (orchestrator
# 自 emit 范式, 见 l2/orchestrator._build_orch_nodetouched), 也兼容 native 形态。非 World-B 源
# → (None, {})。与 orchestrator._unwrap_stub_rewrap / _pending_escalations_from 同款形态契约
# (l0 不 import l2 — 责任分层; 形态契约一致, 字段名 responds_to_escalation_event_id / goal_session_id
# 跟那边对齐不漂)。
def _world_b_escalation_event(ev: EventRecord) -> tuple[str | None, dict[str, object]]:
    payload = ev.payload if isinstance(ev.payload, dict) else {}
    if ev.event_type is EventType.NODE_TOUCHED:
        kind = payload.get("kind")
        orig = payload.get("stub_original_payload")
        if (
            isinstance(kind, str)
            and kind in ("GoalEscalationRaised", "NatureJudgmentCaptured")
            and isinstance(orig, dict)
        ):
            return kind, orig
        return None, {}
    if ev.event_type is EventType.GOAL_ESCALATION_RAISED:
        after = payload.get("after_state")
        return "GoalEscalationRaised", after if isinstance(after, dict) else payload
    if ev.event_type is EventType.NATURE_JUDGMENT_CAPTURED:
        after = payload.get("after_state")
        return "NatureJudgmentCaptured", after if isinstance(after, dict) else payload
    return None, {}


# RUN-038 L0增强 — escalation_lifecycle reducer 喂入事件 (M-2.3 §6.1/§6.7, M-0.2 §9 narrow patch)。
# 此前 _PROJECTION_FILES 声明 escalation_lifecycle.json + 头注 Patch M-2.3-E 但 _apply 无分支
# → Escalation* 事件发了没人读 = 假完整 (stub 文件名注册)。
_ESCALATION_LIFECYCLE_EVENTS = frozenset(
    [
        EventType.ESCALATION_RAISED,
        EventType.ESCALATION_TRIAGED,
        EventType.ESCALATION_RESOLVED,
        EventType.ESCALATION_LEARNING_CAPTURED,
        EventType.ESCALATION_DECISION_MADE,
    ],
)

# K2b-REG (50-graph-protocol §TG.4) — 喂图协议新节点投影的事件: 只消费本步**新定义且自己拥有形态**的
# 两类 (SessionSpawned / LockAcquired)。任一发生 → 全量 re-fold (见 _reduce_node_graphs)。
#
# 刻意**不**消费 (= 诚实交回, 代码与交回一致, 非半接到错 source):
#   - LockReleased: 真生产 payload 是 task/run/entities 形, 无 lock_id → node_reducers 按 lock_id 失活
#     held-by 的路径永远匹配不到 (喂进去要么崩要么 no-op)。release 是交回缺口, 故不消费; lock_graph
#     当前只有 acquire 侧 (held-by 边永远 active, 直到 release 缺口被主 session 补 task/run→lock_id 映射)。
#   - EscalationRaised/Resolved: §6.3 明令 escalation 节点必须源自 World-B (GoalEscalationRaised +
#     NatureJudgmentCaptured), 不是设计态那套 (生产 ≈0)。若在此消费设计态事件 = §6.3 自己点名的
#     "看着上图其实空/错源" 假完成。故不消费 → escalation_graph 是空占位 (slot 在, World-B source 待接)。
#     escalation 事件仍正常喂既有 escalation_lifecycle reducer (零改), 不分叉到 node-graph。
_NODE_GRAPH_EVENTS = frozenset(
    [
        EventType.SESSION_SPAWNED,
        EventType.LOCK_ACQUIRED,
    ],
)


# TaskRunCompleted.outcome → task_graph node status (TaskNode.status enum, M-0.2 §3.2).
# T-L1-36: M-1.4 §3.6 5-outcome (success + 4 可区分 abort) supersede M-0.1 4-outcome
# (failure/partial/escalated 混一 abort)。每个 abort 路径映射到合适的 TaskNode status:
#   aborted_for_replan      → in_progress (task 未完, 回 plan 重排)
#   aborted_unrecoverable   → failed (无法恢复)
#   aborted_external        → failed (外部因素中止)
#   aborted_advisor_decision→ escalated (advisor 决策中止, 决策者介入)
# 旧 4 枚举值保留为 deprecated_alias (cold replay / 历史事件 normalize) — canonical emit 只产新值。
_TASK_RUN_STATUS = {
    "success": "completed",
    "aborted_for_replan": "in_progress",
    "aborted_unrecoverable": "failed",
    "aborted_external": "failed",
    "aborted_advisor_decision": "escalated",
    # deprecated_alias — M-0.1 v1 4-outcome 的历史事件 cold replay (Conflict 11 canonical parse)
    "failure": "failed",
    "escalated": "escalated",
    "partial": "in_progress",
}


def _apply_canonical_transition(
    node: dict[str, object],
    target: ObligationCanonicalState,
) -> None:
    """Cap1 (RUN-038, M-0.6 §3.2) — apply node.canonical_state := target only when legal.

    Reads the node's current canonical_state, defers to the lifecycle state machine
    (is_legal_canonical_transition), and writes target only for legal transitions. Illegal
    transitions out of a terminal state (superseded / retired) are silently refused — the
    canonical_state is left untouched so terminal obligations cannot be revived / cross-moved.
    The scoped audit trail (last_observed_lifecycle_event_type) is updated by the caller
    regardless, so the observed event is not lost.
    """
    from towow.l0.obligations.lifecycle import is_legal_canonical_transition

    raw_current = node.get("canonical_state")
    try:
        current = ObligationCanonicalState(str(raw_current))
    except ValueError:
        # unknown / missing canonical_state — fall back to active (post-Captured default)
        current = ObligationCanonicalState.ACTIVE
    if is_legal_canonical_transition(current, target):
        node["canonical_state"] = target.value


def _endpoint(raw: object) -> dict[str, object]:
    """Map a producer @-reference endpoint {type,id,field_path} → projection {entity_type,...}."""
    if not isinstance(raw, dict):
        return {"entity_type": None, "entity_id": None, "field_path": None}
    return {
        "entity_type": raw.get("type", raw.get("entity_type")),
        "entity_id": raw.get("id", raw.get("entity_id")),
        "field_path": raw.get("field_path"),
    }


def _as_list(value: object) -> list[dict[str, object]]:
    """Coerce a loaded JSON value into list[dict] for reducer manipulation."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _canonical_json(value: object) -> str:
    """Stable canonical serialization for projection equality (T-L3kc-07 validate compare)."""
    return json.dumps(value, sort_keys=True, default=str)


def _entity_history_summary(record: EventRecord) -> str:
    """Best-effort one-line summary for a get_entity_history (§4.1.3) entry.

    event_type + the most identifying payload field (id-ish key) when present, else just the type.
    Never raises — entity history is a read surface, a malformed payload still yields the type.
    """
    payload = record.payload if isinstance(record.payload, dict) else {}
    for key in ("obligation_id", "concept_id", "task_id", "finding_id", "capability_id", "plan_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return f"{record.event_type.value} ({key}={value})"
    return record.event_type.value


def os_replace_with_fsync(src: Path, dst: Path, parent_dir: Path) -> None:
    """Atomic rename + parent dir fsync — durable atomic file replace."""
    src.replace(dst)
    # fsync the directory to ensure rename is durable
    dir_fd = os.open(str(parent_dir), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def build_obligation_lifecycle(
    nodes: list[ObligationLifecycleNode],
) -> ObligationLifecycleStateProjection:
    """Construct a typed projection from a list of nodes (test/debug helper)."""
    return ObligationLifecycleStateProjection(obligations=nodes)


# ─────────────────────────────────────────────────────────────────────────────
# T-R01-7 — concept_graph 多父 DAG 结构 + 共变回填 (c-concept-graph-dag-multiparent-structure@v1)
#
# 概念图目标结构 = 多父 DAG (非严格单父树): 边从两处长 —— create 时声明的 build-on 关系 +
# evolutionary-coupling-mining 挖的共变。图随用从碎块 (孤立块) 自然收敛成连通 DAG (结构自愈)。
# 不变量: 允许一概念 build-on 多基础 (多父)、允许显式 justified 新根、孤立节点数随共变回填单调下降。
# 这里只放纯结构函数 (不碰事件日志); 事件级篮子源在 ProjectionStore.concept_graph_coupling_backfill。
# ─────────────────────────────────────────────────────────────────────────────


def graph_connectivity(
    node_ids: Collection[str],
    edges: Sequence[tuple[str, str]],
) -> tuple[int, int]:
    """无向连通统计: 返回 (连通块数, 孤立节点数)。

    "是否连通 / 孤立" 是无向问题 (概念图回填后是有向 DAG, 但孤立度量看无向连通)。只算两端皆在
    node_ids 的边; 自环忽略。孤立节点 = 自成一块 (size==1) 的节点。
    """
    parent: dict[str, str] = {n: n for n in node_ids}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # 路径压缩
            parent[x], x = root, parent[x]
        return root

    for s, t in edges:
        if s in parent and t in parent and s != t:
            rs, rt = find(s), find(t)
            if rs != rt:
                parent[rs] = rt
    sizes: dict[str, int] = defaultdict(int)
    for n in node_ids:
        sizes[find(n)] += 1
    n_components = len(sizes)
    n_isolated = sum(1 for size in sizes.values() if size == 1)
    return n_components, n_isolated


def backfill_coupling_edges_into_dag(
    node_ids: Collection[str],
    active_edges: Sequence[tuple[str, str]],
    association_edges: Sequence[tuple[str, str, float]],
    *,
    order_key: Callable[[str], object] | None = None,
) -> list[tuple[str, str, float]]:
    """把共变关联边以**多父 DAG**方式回填, 返回新增有向边 (source, target, strength)。

    - 只回填两端皆为已知概念、且当前**无直接边** (任一方向) 的关联对 (不重复既有 build-on 边);
    - 用 order_key 把每条无向关联边定向 u→v (order_key 小→大, 同序按 id) —— 语义 "较新概念
      build-on 较老基础" (默认 order_key = 按 concept_id 字典序; 调用方可传创建序);
    - 加边前做无环校验: 若 v 已能到达 u (加 u→v 会成环) → 跳过该边, 保证结果仍是 DAG;
    - 不限制入度 → 允许一概念被多条边指向 (多父);
    - 无共变信号的概念不被强加父 → 保持显式 justified 孤根 (回填不编造边)。

    association_edges 已是 T-R01-2 miner.association_edges() 的去重去假阳性产物 (无向, 按
    strength 降序); 本函数只负责 "定向 + 无环 + 去重 + 接进图", 不重挖共变 (不造轮子)。
    """
    if order_key is None:
        order_key = lambda n: n  # noqa: E731 — 默认按 concept_id 字典序定向
    nodes = set(node_ids)
    adj: dict[str, set[str]] = defaultdict(set)
    linked: set[frozenset[str]] = set()
    for s, t in active_edges:
        if s in nodes and t in nodes and s != t:
            adj[s].add(t)
            linked.add(frozenset((s, t)))

    def reaches(src: str, dst: str) -> bool:
        """src 沿当前有向边能否到达 dst (无环校验用)。"""
        stack = [src]
        seen = {src}
        while stack:
            x = stack.pop()
            for y in adj[x]:
                if y == dst:
                    return True
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        return False

    added: list[tuple[str, str, float]] = []
    for a, b, strength in association_edges:
        if a not in nodes or b not in nodes or a == b:
            continue
        if frozenset((a, b)) in linked:
            continue  # 已有直接边 (任一方向) → 不重复回填
        u, v = (a, b) if (order_key(a), a) <= (order_key(b), b) else (b, a)
        if reaches(v, u):
            continue  # 加 u→v 会成环 → 跳过, 保 DAG
        adj[u].add(v)
        linked.add(frozenset((u, v)))
        added.append((u, v, strength))
    return added


@dataclass(frozen=True)
class CouplingBackfillResult:
    """一次共变回填的产物 + 前后连通度量 (done_criterion: 孤立 / 连通块下降)。"""

    new_edges: tuple[tuple[str, str, float], ...]  # 新增有向边 (source, target, strength)
    association_edge_count: int  # T-R01-2 miner 产的关联边数 (复用证据, 非另起一套)
    components_before: int
    components_after: int
    isolated_before: int
    isolated_after: int


def derive_coupling_backfill(
    node_ids: Collection[str],
    active_edges: Sequence[tuple[str, str]],
    baskets: Sequence[Collection[str]],
    *,
    order_key: Callable[[str], object] | None = None,
    min_support: int = 1,
) -> CouplingBackfillResult:
    """挖共变 (复用 T-R01-2 EvolutionaryCouplingMiner) → 多父 DAG 回填 → 前后连通度量。

    relatedness 判据 (concept c-concept-coupling-cocreation-relatedness@v1): 本回填的篮子源 =
    "同一共识 brief 批次共同创建的概念" (ConceptCreated 按 source_brief_event_id 分组)。结构事实:
    每个概念只在唯一一个 brief 被创建 → 只属于一个篮子 → item_support 恒 = 1, 于是
    lift = pair_support·total / (1·1) = total 对所有同篮 pair 恒 ≥ min_lift(2.0)、corresponds 恒
    True —— **lift 对应性检查在此篮子源上是结构性空操作 (无 hub 可剪, 一个假阳性都剪不掉), 不是本
    回填的判据**。lift 去假阳性是为 git/envelope 共变篮子源设计的 (那里大文件 hub 的 item_support
    高、lift 塌到 ≤1.8, 见 c-evolutionary-coupling-mining@v1), 不适用于此 brief 分组源。本回填
    实际采用的 relatedness 判据 = **同一共识 brief 批次共同创建** (concepts designed together in
    one frozen consensus = 结构相关), 服务 c-concept-graph-dag-multiparent-structure@v1 的概念图
    结构自愈 (孤立块单调下降)。min_support 必须保持 = 1 (改 ≥2 会毙掉所有 support=1 的同批边、毁掉
    本能力); 要更强的语义对应性 (如 T-R01-5 的 HDC 语义阈值) 留待 T-R01-9 接线到生产消费方时按
    消费方需求决定。
    """
    # 懒导入避免 l0→l1 import-time 耦合 (同 get_events_in_range 对 cold_lookup 的懒导入范式);
    # evolutionary_coupling 只依赖 stdlib, 无回指 l0 的循环风险。
    from towow.l1.cia.evolutionary_coupling import EvolutionaryCouplingMiner

    result = EvolutionaryCouplingMiner(min_support=min_support).mine(
        [frozenset(b) for b in baskets]
    )
    assoc = result.association_edges()
    new_edges = backfill_coupling_edges_into_dag(
        node_ids, active_edges, assoc, order_key=order_key
    )
    comps_before, iso_before = graph_connectivity(node_ids, list(active_edges))
    comps_after, iso_after = graph_connectivity(
        node_ids, [*active_edges, *[(s, t) for s, t, _ in new_edges]]
    )
    return CouplingBackfillResult(
        new_edges=tuple(new_edges),
        association_edge_count=len(assoc),
        components_before=comps_before,
        components_after=comps_after,
        isolated_before=iso_before,
        isolated_after=iso_after,
    )


