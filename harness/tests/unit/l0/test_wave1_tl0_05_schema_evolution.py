"""波1 T-L0-05: Schema Evolution reader policy — unknown-version 按 base_classification 分级 (M-0.1 §5.2).

源表 done_criteria: reader 遇未知 schema 版本的 immutable_truth 事件时 fail-closed(报错停), 遇
abstractable 才 warn/skip; 当前 except:continue 静默被替换。spec §5.2 reader_behavior:
unknown_immutable_truth→fail_replay / unknown_abstractable_process→warn_and_skip / unknown_discardable_noise→skip。

现状(改前): version.py 只有 CURRENT_SCHEMA_VERSION, reader 不查版本 → unknown-version 事件被当
正常 yield(吞)。本组证: known 版本正常读; unknown immutable_truth fail-closed(抛 SchemaEvolutionError);
unknown abstractable warn+skip; unknown discardable 静默 skip。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from towow.l0.event_log import EventLog, SchemaEvolutionError
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
from towow.schemas.version import is_known_schema_version, unknown_version_reader_action

if TYPE_CHECKING:
    from pathlib import Path


def _write_record_line(log_path: Path, *, schema_version: str, classification: BaseClassification) -> str:
    """构造一条 path-B record(指定 schema_version + base_classification) 追加到日志, 返回 event_id。"""
    rec = EventRecord(
        event_id=f"evt-{uuid.uuid4().hex}",
        sequence_number=1,
        timestamp=datetime.now(UTC),
        record_hash=f"sha256:{uuid.uuid4().hex}",
        local_intent_id=f"li-{uuid.uuid4().hex[:8]}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"kind": "test"},
        provenance=Provenance(actor_type=ActorType.SYSTEM.value, actor_id="test"),
        base_classification=classification,
        supersede=Supersede(is_supersede=False),
        subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c", role=SubjectRole.PRIMARY)],
        schema_version=schema_version,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(rec.model_dump_json() + "\n")
    return rec.event_id


# ─── 纯函数策略 ───────────────────────────────────────────────────────────────


def test_reader_action_per_classification() -> None:
    assert is_known_schema_version("1.0.0") is True
    assert is_known_schema_version("9.9.9") is False
    assert unknown_version_reader_action(BaseClassification.IMMUTABLE_TRUTH) == "fail_replay"
    assert unknown_version_reader_action(BaseClassification.ABSTRACTABLE_PROCESS) == "warn_and_skip"
    assert unknown_version_reader_action(BaseClassification.DISCARDABLE_NOISE) == "skip"


# ─── reader 行为 ──────────────────────────────────────────────────────────────


def test_known_version_reads_normally(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    eid = _write_record_line(log_path, schema_version="1.0.0", classification=BaseClassification.DISCARDABLE_NOISE)
    assert any(r.event_id == eid for r in EventLog(log_path).all_records())


def test_unknown_immutable_truth_fail_replay(tmp_path: Path) -> None:
    """unknown-version immutable_truth → reader fail-closed 抛 SchemaEvolutionError (不静默吞真相)。"""
    log_path = tmp_path / "events.log"
    _write_record_line(log_path, schema_version="9.9.9", classification=BaseClassification.IMMUTABLE_TRUTH)
    with pytest.raises(SchemaEvolutionError, match=r"9\.9\.9"):
        EventLog(log_path).all_records()  # 读路径遇未知版本事实源即抛


def test_unknown_abstractable_warn_and_skip(tmp_path: Path) -> None:
    """unknown-version abstractable → warn + skip (不抛, 不进 all_records)。"""
    log_path = tmp_path / "events.log"
    eid = _write_record_line(
        log_path, schema_version="9.9.9", classification=BaseClassification.ABSTRACTABLE_PROCESS,
    )
    with pytest.warns(UserWarning, match="unknown-version"):
        recs = EventLog(log_path).all_records()
    assert all(r.event_id != eid for r in recs), "unknown abstractable 被 skip, 不进读出"


def test_unknown_discardable_silently_skipped(tmp_path: Path) -> None:
    """unknown-version discardable → 静默 skip (不抛不 warn, 不进 all_records)。"""
    log_path = tmp_path / "events.log"
    eid = _write_record_line(
        log_path, schema_version="9.9.9", classification=BaseClassification.DISCARDABLE_NOISE,
    )
    recs = EventLog(log_path).all_records()
    assert all(r.event_id != eid for r in recs)
