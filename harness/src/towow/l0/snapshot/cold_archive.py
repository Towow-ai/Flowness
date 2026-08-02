"""M-0.7 §7.2 — Hot → Cold 物理动作 Prepare / Commit / Finalize 三段式 (M-0.7a Patch 1).

# spec source:
#   03-l0-truth-source/M-0.7-snapshot-consolidation-detailed-design.md §7.2 / §11.9:
#     物理动作不能先于 accepted event 成为最终状态. 三段式:
#       prepare  (consolidation work): 把 hot segment copy 到 staging cold tmp (gzip), 算
#                 source_hash/archive_hash; 不更新 index, 不删 hot, 不改 M-0.1 可见位置.
#       commit   (M-0.5 commit gate): verify staging 存在且 hash 匹配 + hot 未改动 → accepted batch
#                 写 ArchiveSegmentMoved (跟 CrossRunConsolidationCommitted 原子). [verify 见 §7.2
#                 verify_archive_prepared; 本模块给 verify primitive, gate wiring 归 M-0.5/驱动层]
#       finalize (accepted 之后): atomic rename staging→cold + 更新 index hot→cold + 最后 unlink hot
#                 + 记 finalize_completed.
#       reject: 删 staging; hot/index 从未改变 (不存在 "半事实").
#
# 数据安全核心: prepare 归档的是 hot segment 的 **原始字节行** (verbatim), 不重新序列化 event —— 这给
# 最强 round-trip 保证 (hot → cold → 解压 逐字节相同, 零序列化漂移). source_hash 是 hot 文件字节的
# sha256; archive_hash 是 staging .gz 字节的 sha256 (commit verify 用它认 staging 没被篡改).
#
# Daemon-independent: 这些都是被 driver (consolidation run / maintenance agent / CLI) 调用的物理 IO
# 函数, 自身不调度 "何时归档" (那是 §8 maintenance daemon, gated 留债).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from towow.l0.event_log.segments import segment_number_of
from towow.l0.projection.projection import os_replace_with_fsync
from towow.l0.snapshot.cold_storage import (
    cold_archive_path,
    cold_dir,
    cold_staging_dir,
    cold_staging_path,
    update_cold_index,
)

if TYPE_CHECKING:
    from towow.l0.event_log.event_log import EventLog

_SNAPSHOT_MODULE_DIRNAME = "snapshot-module"
_FINALIZE_LOG_NAME = "finalize-log.json"


@dataclass(frozen=True)
class PreparedArchive:
    """Result of prepare — mirrors §7.2 / §2.4 prepared_artifact_ref (the staging artifact + hashes).

    ``source_hash`` is sha256 of the (unchanged) hot segment bytes; ``archive_hash`` is sha256 of the
    staging .gz bytes. ``hot_path_unchanged`` records the hot path so finalize/verify can confirm it
    has not been modified between prepare and commit (§7.2 verify_archive_prepared 验证 2).
    """

    segment_id: int
    staging_path: str
    source_hash: str
    archive_hash: str
    hot_path_unchanged: str
    original_event_count: int


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _fsync_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _non_empty_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def prepare_archive_to_cold(towow_dir: Path, hot_path: Path) -> PreparedArchive:
    """§7.2 prepare — copy a sealed hot segment to staging cold tmp (gzip). Does NOT touch hot/index.

    Archives the hot segment's raw bytes verbatim (each non-empty line, gzip-compressed) so the
    cold copy is a byte-identical record of the segment — no re-serialization drift. Computes the
    source hash (hot bytes) and archive hash (staging .gz bytes). M-0.1 visible state is untouched:
    hot file unchanged, no index update, no final cold archive yet.
    """
    seg = segment_number_of(hot_path)
    if seg is None:
        msg = f"not an archivable rotated hot segment: {hot_path}"
        raise ValueError(msg)

    raw = hot_path.read_text(encoding="utf-8")
    lines = _non_empty_lines(raw)
    source_hash = _sha256_of_file(hot_path)

    staging = cold_staging_path(towow_dir, seg)
    cold_staging_dir(towow_dir).mkdir(parents=True, exist_ok=True)
    # write the raw lines verbatim under gzip — byte-for-byte preservation (mtime=0 for determinism)
    with gzip.GzipFile(filename=str(staging), mode="wb", mtime=0) as gz:
        for ln in lines:
            gz.write(ln.encode("utf-8") + b"\n")
    _fsync_file(staging)
    archive_hash = _sha256_of_file(staging)

    return PreparedArchive(
        segment_id=seg,
        staging_path=str(staging),
        source_hash=source_hash,
        archive_hash=archive_hash,
        hot_path_unchanged=str(hot_path),
        original_event_count=len(lines),
    )


def verify_prepared_archive(prepared: PreparedArchive) -> str | None:
    """§7.2 commit verify primitive — None iff staging untampered + hot unchanged. Else failure str.

    M-0.5 commit gate calls this (alongside §6.4 consolidation invariants) before accepting the
    ArchiveSegmentMoved event: the physical action must not become final state ahead of the accepted
    event, and the accepted event must reference exactly the prepared bytes.
    """
    staging = Path(prepared.staging_path)
    if not staging.exists():
        return "staging missing"
    if _sha256_of_file(staging) != prepared.archive_hash:
        return "staging tampered"
    hot = Path(prepared.hot_path_unchanged)
    if not hot.exists():
        return "hot segment missing"
    if _sha256_of_file(hot) != prepared.source_hash:
        return "hot segment modified during prepare"
    return None


def finalize_archive(
    towow_dir: Path,
    prepared: PreparedArchive,
    *,
    event_ids: list[str],
) -> None:
    """§7.2 finalize (accepted 之后) — atomic rename staging→cold + 更新 index + unlink hot. Idempotent.

    Order matters for crash safety (§7.2): rename first (cold appears), then index, then unlink hot
    last. A crash between any two steps is recoverable by re-running finalize (recover_after_crash):
      - staging still present  → rename + index + unlink (continue from before rename)
      - cold present, hot present → index + unlink (continue from before unlink)
      - cold present, hot gone  → already finalized; just (re)assert index + finalize-log
    """
    cold_path = cold_archive_path(towow_dir, prepared.segment_id)
    staging = Path(prepared.staging_path)
    hot = Path(prepared.hot_path_unchanged)

    # 1. atomic rename staging → final cold (same-dir rename is atomic). Skip if already renamed.
    if staging.exists() and not cold_path.exists():
        cold_dir(towow_dir).mkdir(parents=True, exist_ok=True)
        os_replace_with_fsync(staging, cold_path, cold_dir(towow_dir))
    elif staging.exists() and cold_path.exists():
        # cold already materialized (a prior finalize got this far); the staging is now orphan — drop it
        staging.unlink()

    # 2. update index hot → cold (idempotent: same ids → same segment)
    update_cold_index(towow_dir, event_ids=event_ids, segment_id=prepared.segment_id)

    # 3. unlink hot last
    if hot.exists():
        hot.unlink()
        if hot.parent.is_dir():
            _fsync_dir(hot.parent)

    # 4. record finalize completed (persisted in snapshot-module/finalize-log.json)
    record_finalize_completed(towow_dir, prepared)


def rollback_prepared_archive(towow_dir: Path, prepared: PreparedArchive) -> None:
    """§7.2 reject 路径 — 删 staging; hot/index 从未改变 (no half-truth). Safe if staging already gone."""
    staging = Path(prepared.staging_path)
    if staging.exists():
        staging.unlink()
    # hot was never modified; index was never written — nothing to roll back.


# ─── crash / forbidden recovery (§7.2 recover_after_crash) ────────────────────────


@dataclass(frozen=True)
class RecoveryFinding:
    """A forbidden / inconsistent state recovery refuses to paper over (§7.2 forbidden recovery).

    Recovery never synthesizes an ArchiveSegmentMoved event (event log is the sole source of truth).
    It surfaces the anomaly as a finding for the driver (maintenance agent / audit fork) to escalate
    through the proper FindingCreated path — recovery itself writes zero events.
    """

    kind: str  # "archive_recovery_inconsistent" | "archive_recovery_forbidden_cold"
    segment_id: int
    detail: str


@dataclass(frozen=True)
class RecoveryReport:
    """Outcome of recover_after_crash — what was finalized, which orphans removed, what was flagged."""

    finalized_segments: list[int]
    orphan_staging_removed: list[str]
    findings: list[RecoveryFinding]


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):  # bool is an int subclass — exclude
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return default


def _prepared_from_payload(payload: dict[str, object]) -> PreparedArchive | None:
    """Reconstruct a PreparedArchive from an accepted ArchiveSegmentMoved.prepared_artifact_ref."""
    ref = payload.get("prepared_artifact_ref")
    if not isinstance(ref, dict):
        return None
    required = ("segment_id", "staging_path", "source_hash", "archive_hash", "hot_path_unchanged")
    if any(k not in ref for k in required):
        return None
    return PreparedArchive(
        segment_id=_as_int(ref["segment_id"]),
        staging_path=str(ref["staging_path"]),
        source_hash=str(ref["source_hash"]),
        archive_hash=str(ref["archive_hash"]),
        hot_path_unchanged=str(ref["hot_path_unchanged"]),
        original_event_count=_as_int(payload.get("original_event_count", 0)),
    )


def _event_ids_in_jsonl_text(text: str) -> list[str]:
    out: list[str] = []
    for ln in _non_empty_lines(text):
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        eid = obj.get("event_id") if isinstance(obj, dict) else None
        if isinstance(eid, str):
            out.append(eid)
    return out


def _archived_event_ids(towow_dir: Path, prepared: PreparedArchive) -> list[str]:
    """The event_ids physically in this segment — from hot if present, else the cold archive.

    Recovery may run after hot is already gone (crash stage C), so the surviving cold .gz is the
    fallback source for the index update. Either source is the same byte-preserved JSONL.
    """
    hot = Path(prepared.hot_path_unchanged)
    if hot.exists():
        return _event_ids_in_jsonl_text(hot.read_text(encoding="utf-8"))
    cold_path = cold_archive_path(towow_dir, prepared.segment_id)
    if cold_path.exists():
        with gzip.open(cold_path, "rb") as f:
            return _event_ids_in_jsonl_text(f.read().decode("utf-8"))
    staging = Path(prepared.staging_path)
    if staging.exists():
        with gzip.open(staging, "rb") as f:
            return _event_ids_in_jsonl_text(f.read().decode("utf-8"))
    return []


def recover_after_crash(towow_dir: Path, event_log: EventLog) -> RecoveryReport:
    """§7.2 — reconcile physical cold-storage state with the accepted ArchiveSegmentMoved events.

    For every accepted ArchiveSegmentMoved not yet recorded finalize-completed, redo finalize per
    the surviving physical state (idempotent: staging present → rename+index+unlink; cold+hot →
    index+unlink; cold only → record completed; nothing → inconsistent finding). Orphan staging
    files not referenced by any accepted event are unlinked. Cold archives with NO accepted event
    are the forbidden-recovery case: surfaced as a finding, never patched by writing an event.

    M-0.7 v3.6+ candidate 乙 (DESIGN-M07-v3.6 §4.2): an ArchiveSegmentMoved with
    ``rewritten_hot_segment=true`` is a COMPACTION move — after the whole segment went verbatim to
    cold, a "thin segment" carrying only the pinned events was written back to hot at the SAME seg号.
    Its terminal state is "cold archive present + hot file (the thin segment) present", which the
    standard finalize would WRONGLY treat as "crash before unlink hot" and delete the thin segment.
    So these are routed to ``recover_compaction_after_crash`` (segment_compaction.py) — handled there,
    skipped here — and they still count as referenced_segments so the forbidden-cold check below does
    not flag their cold archive.

    Reads only the accepted ArchiveSegmentMoved events from M-0.1 — it writes zero events itself.
    """
    from towow.schemas.enums import EventType  # local import: keep the enum dependency lazy

    accepted = event_log.get_events_by_type(EventType.ARCHIVE_SEGMENT_MOVED)
    accepted_prepared: list[PreparedArchive] = []
    compaction_prepared: list[PreparedArchive] = []
    for rec in accepted:
        prepared = _prepared_from_payload(rec.payload)
        if prepared is None:
            continue
        if rec.payload.get("rewritten_hot_segment") is True:
            # candidate 乙 compaction move — owned by recover_compaction_after_crash; still referenced.
            compaction_prepared.append(prepared)
        else:
            accepted_prepared.append(prepared)

    finalized: list[int] = []
    findings: list[RecoveryFinding] = []
    # both ordinary cold-MOVE and compaction moves reference their segment + staging (so neither is
    # mis-flagged as an orphan staging file or a forbidden cold archive below).
    referenced_segments = {p.segment_id for p in accepted_prepared} | {
        p.segment_id for p in compaction_prepared
    }
    referenced_staging = {Path(p.staging_path).name for p in accepted_prepared} | {
        Path(p.staging_path).name for p in compaction_prepared
    }

    for prepared in accepted_prepared:
        if is_finalize_completed(towow_dir, prepared):
            continue  # already done — skip (no duplicate processing)
        cold_path = cold_archive_path(towow_dir, prepared.segment_id)
        staging = Path(prepared.staging_path)
        hot = Path(prepared.hot_path_unchanged)
        if staging.exists() or (cold_path.exists() and hot.exists()) or (
            cold_path.exists() and not hot.exists()
        ):
            # any of the three recoverable states → finalize is idempotent for all of them
            finalize_archive(towow_dir, prepared, event_ids=_archived_event_ids(towow_dir, prepared))
            finalized.append(prepared.segment_id)
        else:
            # neither staging nor cold survives, hot gone — cannot recover; refuse to fabricate
            findings.append(
                RecoveryFinding(
                    kind="archive_recovery_inconsistent",
                    segment_id=prepared.segment_id,
                    detail="accepted ArchiveSegmentMoved but no staging/cold/hot artifact survives",
                ),
            )

    # orphan staging: a staging file not referenced by any accepted event → safe to drop
    orphan_removed: list[str] = []
    staging_dir = cold_staging_dir(towow_dir)
    if staging_dir.is_dir():
        for f in sorted(staging_dir.iterdir()):
            if f.is_file() and f.name not in referenced_staging:
                f.unlink()
                orphan_removed.append(str(f))

    # forbidden: a cold archive with NO accepted ArchiveSegmentMoved → finding, never a synthesized event
    cd = cold_dir(towow_dir)
    if cd.is_dir():
        for f in sorted(cd.iterdir()):
            if not (f.is_file() and f.name.startswith("archive-") and f.name.endswith(".jsonl.gz")):
                continue
            seg = segment_number_of(Path(f.name.replace("archive-", "events-").removesuffix(".gz")))
            if seg is None:
                continue
            if seg not in referenced_segments:
                findings.append(
                    RecoveryFinding(
                        kind="archive_recovery_forbidden_cold",
                        segment_id=seg,
                        detail=f"cold archive {f.name} has no accepted ArchiveSegmentMoved event",
                    ),
                )

    return RecoveryReport(
        finalized_segments=finalized,
        orphan_staging_removed=orphan_removed,
        findings=findings,
    )


# ─── finalize-log (§7.2: persisted in snapshot-module/finalize-log.json) ──────────


def _finalize_log_path(towow_dir: Path) -> Path:
    return towow_dir / _SNAPSHOT_MODULE_DIRNAME / _FINALIZE_LOG_NAME


def read_finalize_log(towow_dir: Path) -> list[str]:
    """Read the finalize-log's completed keys; missing/corrupt → [] (fail-safe).

    The finalize-log persists which ArchiveSegmentMoved finalizes have completed (§7.2 / §7.2 crash
    recovery: a finalize not in this list is treated as pending). Stored as
    ``{"completed": [<key>...]}``; this returns just the keys, filtering out non-str entries.
    """
    path = _finalize_log_path(towow_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    completed = data.get("completed")
    if not isinstance(completed, list):
        return []
    return [k for k in completed if isinstance(k, str)]


def _finalize_key(prepared: PreparedArchive) -> str:
    # key a finalized archive by its segment id (1:1 archive↔segment, §7.1) + archive_hash so a
    # re-prepared/replaced segment is a distinct entry.
    return f"seg-{prepared.segment_id}:{prepared.archive_hash}"


def record_finalize_completed(towow_dir: Path, prepared: PreparedArchive) -> None:
    """Mark this prepared archive's finalize as completed (idempotent — set semantics)."""
    completed = read_finalize_log(towow_dir)
    key = _finalize_key(prepared)
    if key not in completed:
        completed.append(key)
    path = _finalize_log_path(towow_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"completed": completed}, indent=2, sort_keys=True), encoding="utf-8")
    os_replace_with_fsync(tmp, path, path.parent)


def is_finalize_completed(towow_dir: Path, prepared: PreparedArchive) -> bool:
    """True iff this prepared archive was recorded finalize-completed."""
    return _finalize_key(prepared) in read_finalize_log(towow_dir)


__all__ = [
    "PreparedArchive",
    "RecoveryFinding",
    "RecoveryReport",
    "finalize_archive",
    "is_finalize_completed",
    "prepare_archive_to_cold",
    "read_finalize_log",
    "record_finalize_completed",
    "recover_after_crash",
    "rollback_prepared_archive",
    "verify_prepared_archive",
]
