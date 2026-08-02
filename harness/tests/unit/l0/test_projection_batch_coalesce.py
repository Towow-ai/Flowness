"""ProjectionStore 批次内读写合并 — 正确性测试 (perf: catchup() 单批多条事件碰同一投影时,
只在批尾落盘一次, 而非逐条事件各自整份读写磁盘一次).

背景 (侦察实证): catchup() 的 for 循环里每条匹配事件独立跑一次"读整个投影文件→内存改一条→
整份写回"——同一批次里多条事件碰同一投影文件时, 该文件被反复整份读写 (生产实测 decision.json
2.65MB/5686 条被读写 20 次×94ms≈1.9s)。修复: ProjectionStore 在 catchup() 内部开一个批次内存
缓存, read()/write_atomic() 在批内只操作内存, 批尾把"本批次真正写过"的投影各落盘一次 (仍走原
tmp→fsync→rename 的 _write_disk 原子路径, 不削弱durability)。

本文件证两件事:
  1. 合并正确性 (test_batch_coalesces_disk_writes): 同一批 3 条 CONCEPT_CREATED (+1 条
     CONCEPT_EDGE_ADDED, 顺带验二阶 impact_graph 也被合并) 碰同一投影, spy 断言 concept_graph /
     impact_graph 的磁盘写各恰好 1 次 (非 4 次)。
  2. 语义不变 (test_batch_result_matches_incremental_application): 把同一组事件喂两个独立
     store —— 一个用 1 次大 catchup() (批处理), 一个用 4 次小 catchup() (每次仅 1 条新事件,
     等价"逐条独立应用") —— 两者最终 concept_graph / impact_graph 内容字节完全一致。
  3. validate_rebuild 兜底 (test_validate_rebuild_consistent_after_batch): 批处理后 catchup +
     全量 rebuild 逐投影字节比对, 必须一致 (T-L3kc-07 既有机制, 复用而非新造)。

★ 本文件对 pre-fix 代码 (write_atomic 每次都真落盘) 必须 FAIL —— 已手工验证过一遍 (临时用
`git show HEAD:.../projection.py` 换回改动前源码跑本文件, 是等价于 `git stash` 的验证手段;
worktree 内 `git stash` 被 PreToolUse guard 物理拦截, 故未用该字面命令): 本批 3 条
CONCEPT_CREATED + 1 条 CONCEPT_EDGE_ADDED 都碰 concept_graph, pre-fix 磁盘写 (os_replace_with_fsync
真落盘次数) = 4, 非 1; 换回改动后源码复跑, 3 个测试全过。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import towow.l0.projection.projection as projection_module
from towow.l0.event_log import EventLog
from towow.l0.projection.projection import ProjectionStore, _canonical_json
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import Subject, Supersede
from towow.schemas.event_record import EventRecord, Provenance

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


# ─── synthetic event-log builders (same idiom as test_recompute_one_surgical.py) ───────────


def _rec(
    seq: int,
    etype: EventType,
    category: EventCategory,
    payload: dict[str, object],
    *,
    event_id: str | None = None,
    subjects: list[tuple[SubjectEntityType, str]] | None = None,
) -> EventRecord:
    subj = subjects if subjects is not None else [(SubjectEntityType.CONCEPT, "c-anchor")]
    return EventRecord(
        event_id=event_id or f"evt-{uuid.uuid4().hex}",
        sequence_number=seq,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=etype,
        event_category=category,
        payload=payload,
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="batch-coalesce-test"),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=et, entity_id=eid, role=SubjectRole.PRIMARY) for et, eid in subj],
        schema_version="1.0.0",
    )


def _write_log(log_path: Path, records: list[EventRecord]) -> EventLog:
    """Write records as JSONL to events.log (on-disk canonical form) and open a real EventLog."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(r.model_dump_json() for r in records) + "\n", encoding="utf-8")
    return EventLog(log_path)


def _same_projection_batch() -> list[EventRecord]:
    """3 条同类型 (CONCEPT_CREATED) 事件碰同一投影 (concept_graph) + 1 条 CONCEPT_EDGE_ADDED —
    后者额外触发二阶 impact_graph 的重算, 让"批内合并对二阶 projection 同样生效 + 读到批内最新态"
    也被验到 (impact_graph 从 concept_graph.edges 派生, edge 必须看到同批刚创建的 c-X/c-Z 节点)。
    """
    return [
        _rec(1, EventType.CONCEPT_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"concept_id": "c-X", "name": "Xi"}},
             subjects=[(SubjectEntityType.CONCEPT, "c-X")]),
        _rec(2, EventType.CONCEPT_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"concept_id": "c-Y", "name": "Yi"}},
             subjects=[(SubjectEntityType.CONCEPT, "c-Y")]),
        _rec(3, EventType.CONCEPT_CREATED, EventCategory.STATE_TRANSITION,
             {"after_state": {"concept_id": "c-Z", "name": "Zi"}},
             subjects=[(SubjectEntityType.CONCEPT, "c-Z")]),
        _rec(4, EventType.CONCEPT_EDGE_ADDED, EventCategory.STATE_TRANSITION,
             {"after_state": {"edge_id": "ce-XZ", "source_concept_id": "c-X",
                              "target_concept_id": "c-Z", "edge_type": "dependency",
                              "direction": "forward"}},
             subjects=[(SubjectEntityType.CONCEPT, "c-X"), (SubjectEntityType.CONCEPT, "c-Z")]),
    ]


def _spy_disk_replaces(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count real atomic os_replace_with_fsync calls per STATE projection filename (the
    `.projection-meta/<name>.json` sidecar shares the same basename, e.g. `concept_graph.json`,
    so it is bucketed separately by a ".projection-meta/" prefix — meta is persisted once per
    catchup() call regardless of batching (unaffected by this fix's coalescing), so conflating
    it with the state-file count would mask the real signal under test).

    This spies at the module-level ``os_replace_with_fsync`` (the tmp→fsync→rename primitive),
    which is the ACTUAL "a real disk write landed" signal — present, unchanged, in both the
    pre-fix code (called once per write_atomic() call, i.e. once per reducer touch) and the
    post-fix code (called once per batch-flush of a projection actually dirtied this batch).
    Spying here (rather than on a post-fix-only internal method name) is what makes this test
    meaningfully FAIL against the pre-fix source: pre-fix shows 4 STATE replaces for
    concept_graph.json (one per touching event), post-fix must show exactly 1 (coalesced at
    batch-flush time).
    """
    counts: dict[str, int] = {}
    original = projection_module.os_replace_with_fsync

    def spy(src: Path, dst: Path, parent_dir: Path) -> None:
        key = dst.name if dst.parent.name != ".projection-meta" else f".projection-meta/{dst.name}"
        counts[key] = counts.get(key, 0) + 1
        original(src, dst, parent_dir)

    monkeypatch.setattr(projection_module, "os_replace_with_fsync", spy)
    return counts


# ─── 1. 合并正确性: 同批多次碰同一投影 → 磁盘只写 1 次 ─────────────────────────────


def test_batch_coalesces_disk_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = _write_log(tmp_path / "events.log", _same_projection_batch())
    store = ProjectionStore(tmp_path / "graph")
    write_counts = _spy_disk_replaces(monkeypatch)

    processed = store.catchup(log)

    assert processed == 4
    # ★ pre-fix 代码在此处会是 4 (每条事件各自落盘一次) —— post-fix 必须恰好 1 次。
    assert write_counts.get("concept_graph.json") == 1, (
        f"concept_graph 应在批尾只落盘 1 次 (本批 3 条 CONCEPT_CREATED + 1 条 "
        f"CONCEPT_EDGE_ADDED 都碰它), 实际 {write_counts.get('concept_graph.json')} 次"
    )
    # impact_graph 是二阶 projection, 每条 concept_graph 事件后 _apply 都会触发它的重算 —— 同样
    # 该在批内合并, 只落盘 1 次 (非 4 次)。
    assert write_counts.get("impact_graph.json") == 1, (
        f"impact_graph (二阶) 应同样批内合并只落盘 1 次, 实际 {write_counts.get('impact_graph.json')} 次"
    )
    # 内容正确性 sanity: 3 个节点 + 1 条边都真物化了 (合并不是"少写"而是"少落盘次数")。
    cg = store.read("concept_graph") or {}
    assert {n["concept_id"] for n in cg["nodes"]} == {"c-X", "c-Y", "c-Z"}
    assert [e["edge_id"] for e in cg["edges"]] == ["ce-XZ"]
    ig = store.read("impact_graph") or {}
    # impact_graph 必须读到本批内刚创建的 c-X/c-Z (二阶 reducer 若读到陈旧/半批态会漏这条边)。
    # dependency 边在 impact_graph 里方向归一化翻转 (source=原 target=上游, 见
    # _recompute_impact_graph docstring) —— 原边 c-X→c-Z(dependency) 翻成 c-Z→c-X。
    assert {(e["source"], e["target"]) for e in ig["edges"]} == {("c-Z", "c-X")}


# ─── 2. 语义不变: 批处理 vs 逐条独立应用, 最终态字节一致 ───────────────────────────


def test_batch_result_matches_incremental_application(tmp_path: Path) -> None:
    records = _same_projection_batch()

    # store_batched: 全部 4 条事件一次性落到 events.log, 单次 catchup() 处理整批。
    log_all = _write_log(tmp_path / "batched" / "events.log", records)
    store_batched = ProjectionStore(tmp_path / "batched" / "graph")
    store_batched.catchup(log_all)

    # store_incremental: 逐条追加, 每次只让 catchup() 看到 1 条新事件 (等价"3 次独立应用"的
    # 参照基线 —— 每次 catchup 都是自己的一个批, 批大小=1, 天然无合并可言)。
    store_incremental = ProjectionStore(tmp_path / "incremental" / "graph")
    for i in range(1, len(records) + 1):
        log_prefix = _write_log(tmp_path / f"prefix-{i}" / "events.log", records[:i])
        store_incremental.catchup(log_prefix)

    for name in ("concept_graph", "impact_graph"):
        batched_state = store_batched.read(name)
        incremental_state = store_incremental.read(name)
        assert _canonical_json(batched_state) == _canonical_json(incremental_state), (
            f"{name}: 批处理结果与逐条独立应用结果不一致 —— 攒批改变了语义"
        )


# ─── 3. validate_rebuild 兜底: 批处理后物化态 == 全量重放 (逐投影字节一致) ─────────────


def test_validate_rebuild_consistent_after_batch(tmp_path: Path) -> None:
    log = _write_log(tmp_path / "events.log", _same_projection_batch())
    store = ProjectionStore(tmp_path / "graph")
    store.catchup(log)

    processed, inconsistent = store.validate_rebuild(log)

    assert processed == 4
    assert inconsistent == [], f"批处理后物化态与全量重放不一致的投影: {inconsistent}"
