"""Provenance forward-anchoring spine helper — applies @concept:provenance-anchor-forward-spine@v1.

这是构造 provenance 锚点的**唯一**地方:一条 RunStarted 事件,其 ``subjects[]`` 向前锚定一批 canonical
finding(+ 它们来源的审计 doc 文件)。真正的一次性 down-payment emit 和 live-fire 集成测试都走
``build_provenance_anchor_intent`` / ``emit_provenance_anchor`` —— 测试因此跑的就是"产真锚点那段代码",
两边永不漂移,不是空心地另抄一份锚点形态。

为什么用 RunStarted 扛索引(anchor-forward 而非 backfill):``EventRecord`` frozen,旧 FindingCreated 事件
无法被回填 provenance。改为 emit 一条新锚点事件(更高 seq),其 ``subjects[]`` 同列 finding + file 实体;
``EventLog.get_events_by_entity(entity_type, entity_id)`` 是对每条记录 ``subjects[]`` 的 O(1) 反向索引
(index.py 对**所有 role** 建索引,不止 primary),故新锚点 + 旧 FindingCreated + 未来 fix 事件自动构成
以稳定 ``entity_id`` 为键的双向链。载体分工见 concept provenance-anchor-forward-spine@v1:文件级证据 →
``Subject{entity_type=FILE}`` 挂锚点(RunStarted 扛 subjects),不挂 EvidenceItem。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from collections.abc import Sequence

    from towow.l0.event_log import EventLog
    from towow.schemas.event_record import EventRecord

#: run_type 戳在 provenance 锚点的 RunStarted 上 —— 区分它与 m31 维护 run(snapshot/gc/...)。
ANCHOR_RUN_TYPE = "provenance-anchor"


def build_provenance_anchor_intent(
    *,
    run_id: str,
    finding_ids: Sequence[str],
    doc_paths: Sequence[str],
    started_at: str,
    actor_id: str,
    task_id: str | None = None,
    run_args_extra: dict[str, str] | None = None,
) -> EventIntent:
    """构造向前锚定 ``finding_ids`` + ``doc_paths`` 的 RunStarted 锚点 EventIntent。

    ``subjects[]`` = run task(primary) + 每个 finding 一个 FINDING subject + 每个 doc 一个 FILE subject。
    每个 finding/file 因此经 ``EventLog.get_events_by_entity`` 反向可索引,每个 finding 的 FindingCreated 与
    本锚点解析到同一个键(双向脊柱)。

    无 finding 时 raise ``ValueError`` —— 零 finding subject 的锚点什么都不索引,会是一条静默空脊柱条目。
    """
    if not finding_ids:
        raise ValueError("provenance anchor needs at least one finding subject (empty spine entry)")
    subjects: list[Subject] = [
        Subject(entity_type=SubjectEntityType.TASK, entity_id=run_id, role=SubjectRole.PRIMARY),
    ]
    subjects.extend(
        Subject(entity_type=SubjectEntityType.FINDING, entity_id=fid, role=SubjectRole.AFFECTED) for fid in finding_ids
    )
    subjects.extend(
        Subject(entity_type=SubjectEntityType.FILE, entity_id=path, role=SubjectRole.AFFECTED) for path in doc_paths
    )
    run_args: dict[str, str] = {
        "anchor_kind": "audit-finding-forward-spine",
        "finding_count": str(len(finding_ids)),
        "doc_count": str(len(doc_paths)),
    }
    if run_args_extra:
        run_args.update(run_args_extra)
    payload: dict[str, object] = {
        "run_id": run_id,
        "run_type": ANCHOR_RUN_TYPE,
        "started_at": started_at,
        "run_args": run_args,
    }
    return EventIntent(
        local_intent_id=f"{EventType.RUN_STARTED.value}-{uuid.uuid4().hex[:10]}",
        event_type=EventType.RUN_STARTED,
        event_category=EventCategory.STATE_TRANSITION,
        payload=payload,
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id,
            run_id=run_id,
            task_id=task_id,
        ),
        base_classification=BaseClassification.ABSTRACTABLE_PROCESS,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version="1.0.0",
    )


def emit_provenance_anchor(
    event_log: EventLog,
    *,
    finding_ids: Sequence[str],
    doc_paths: Sequence[str],
    started_at: str,
    actor_id: str,
    run_id: str | None = None,
    task_id: str | None = None,
    run_args_extra: dict[str, str] | None = None,
) -> EventRecord:
    """构造 + path-B ``write_direct`` 锚点,返回已提交的 ``EventRecord``。

    RunStarted 是 path-B-allowed 事件类型(event_log.py ``_is_path_b_allowed``),``write_direct`` 在
    cross-process append 锁内从磁盘 tail 取 seq —— 兄弟会话并发跑时对共享 live 账本调用也安全。
    """
    resolved_run_id = run_id or f"run-{ANCHOR_RUN_TYPE}-{uuid.uuid4().hex[:12]}"
    intent = build_provenance_anchor_intent(
        run_id=resolved_run_id,
        finding_ids=finding_ids,
        doc_paths=doc_paths,
        started_at=started_at,
        actor_id=actor_id,
        task_id=task_id,
        run_args_extra=run_args_extra,
    )
    return event_log.write_direct(intent)
