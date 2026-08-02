"""Cross-process sequence_number integrity regression test (DOGFOOD F-019-12).

Background: M-0.1 §5.2 requires that no two writes ever share a sequence_number. The spec
assumed a single OS process would suffice and prescribed a process-internal mutex. The real
v3 topology breaks that assumption — an always-on daemon and goal-spawn sessions are separate
OS processes appending to the same events.log. With only a threading.Lock, each process derived
the same next_sequence from its own stale in-memory counter and wrote duplicate seqs (empirical:
553 dup seqs across the dogfood events.log, e.g. the RUN-020 window 5910-5913).

This test spawns multiple *real* OS processes (not threads — threads would be masked by the
intra-process mutex) that all append to one shared log simultaneously, then asserts every
sequence_number is unique. It passes only when the cross-process fcntl.flock is actually held
during seq-derive → append. Pre-fix (process-internal mutex only) it produces duplicates.
"""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import time
from pathlib import Path
from typing import Any

import pytest

from towow.l0.event_log import EventLog
from towow.l0.event_log.event_log import build_accepted_batch_e11
from towow.l0.event_log.segments import iter_raw_event_lines
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede


def _node_touched_intent(worker_id: int, idx: int) -> EventIntent:
    """A path-B-allowed NodeTouched intent — mirrors the daemon's high-frequency write stream."""
    return EventIntent(
        local_intent_id=f"w{worker_id}-{idx}",
        event_type=EventType.NODE_TOUCHED,
        event_category=EventCategory.ENVELOPE,
        payload={"target_entity_id": f"c-{worker_id}", "touch_type": "read"},
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.AGENT_FORK.value,
            actor_id=f"fork-{worker_id}",
            task_id="t-conc",
            run_id="r-conc",
            parent_session_id=f"sess-{worker_id}",
            fork_name=f"fork-{worker_id}",
        ),
        base_classification=BaseClassification.DISCARDABLE_NOISE,
        supersede=Supersede(is_supersede=False),
        subjects=[
            Subject(
                entity_type=SubjectEntityType.CONCEPT,
                entity_id=f"c-{worker_id}",
                role=SubjectRole.PRIMARY,
            ),
        ],
        schema_version="1.0.0",
    )


def _concurrent_writer(log_path_str: str, worker_id: int, count: int, start_at: float) -> None:
    """Child-process entrypoint: open the shared log and write `count` events at `start_at`.

    Each process builds its own EventLog (own in-memory counter) — exactly the production
    topology where the daemon and a session are independent processes. Busy-wait to a shared
    wall-clock start so all writers contend on the same window (maximizes the race pre-fix).
    """
    event_log = EventLog(Path(log_path_str))
    now = time.time()
    if start_at > now:
        time.sleep(start_at - now)
    for idx in range(count):
        event_log.write_direct(_node_touched_intent(worker_id, idx))


def _read_seqs(log_path: Path) -> list[int]:
    seqs: list[int] = []
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            seqs.append(json.loads(line)["sequence_number"])
    return seqs


def test_concurrent_processes_never_share_a_sequence_number(tmp_path: Path) -> None:
    """N real processes writing the same log simultaneously → zero duplicate sequence_numbers."""
    log_path = tmp_path / "events.log"
    n_workers = 4
    per_worker = 60
    expected_total = n_workers * per_worker

    ctx = multiprocessing.get_context("spawn")
    start_at = time.time() + 1.0  # give spawn startup headroom so writers overlap
    procs = [
        ctx.Process(
            target=_concurrent_writer,
            args=(str(log_path), wid, per_worker, start_at),
        )
        for wid in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, f"writer exited with {p.exitcode}"

    seqs = _read_seqs(log_path)
    # No physical line was lost or duplicated.
    assert len(seqs) == expected_total, f"expected {expected_total} lines, got {len(seqs)}"
    # The invariant under test: every sequence_number is unique (this is what F-019-12 broke).
    assert len(set(seqs)) == expected_total, (
        f"duplicate sequence_number(s) detected: "
        f"{sorted(s for s in set(seqs) if seqs.count(s) > 1)}"
    )
    # And because every write derives seq from the on-disk tail under the lock, the result is a
    # gap-free 0..N-1 range — strict global monotonic allocation across processes.
    assert set(seqs) == set(range(expected_total))


def test_lock_file_is_created_beside_the_log(tmp_path: Path) -> None:
    """The cross-process lock is a real on-disk fcntl target under the conventional locks/ dir."""
    log_path = tmp_path / "events.log"
    EventLog(log_path)
    assert (tmp_path / "locks" / "events.log.lock").exists()


# ════════════════════════════════════════════════════════════════════════════════
# Strengthening (this task): the original test above proves only "修后不碰撞". The bar
# also wants the "修前会碰撞" half — a *non-vacuity* proof that this harness can detect a
# collision at all (otherwise a green result could just mean the processes never actually
# overlapped). These tests add: a deterministic reconstruction of the pre-fix mechanism
# (must collide), concurrent path-A batch coverage (the fix re-derives seq under the lock
# on path A too), and an A/B that removes only the flock to show it is load-bearing.
# All synchronize with a Barrier (robust) rather than wall-clock, and surface worker
# tracebacks via a queue.
# ════════════════════════════════════════════════════════════════════════════════


def _all_segment_seqs(log_path: Path) -> list[int]:
    """Every physically-present sequence_number across base + hot segments (raw, unfiltered)."""
    seqs: list[int] = []
    for line in iter_raw_event_lines(log_path):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("sequence_number"), int):
            seqs.append(obj["sequence_number"])
    return seqs


def _join_and_assert_no_worker_errors(procs: list[Any], result_q: Any, n_expected: int) -> None:
    for p in procs:
        p.join(timeout=120)
    for p in procs:
        assert p.exitcode == 0, f"writer exited with {p.exitcode}"
    results = [result_q.get(timeout=120) for _ in range(n_expected)]
    errs = [r for r in results if r[0] == "err"]
    assert not errs, "worker error(s):\n" + "\n".join(f"  w{w}: {info}" for _, w, info in errs)


def _append_one_batch(log: EventLog, wid: int, bidx: int) -> None:
    """Append a path-A batch [CONCEPT_CREATED domain, CommitAccepted sentinel] (mirrors
    test_append_accepted_batch_atomically — proven payloads, no mock).
    """
    tag = f"{wid}-{bidx}"
    domain = log.stamp_for_batch(
        EventIntent(
            local_intent_id=f"cc-{tag}",
            event_type=EventType.CONCEPT_CREATED,
            event_category=EventCategory.STATE_TRANSITION,
            payload={"target_entity_id": "c-1", "touch_type": "read"},
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.AGENT_FORK.value,
                actor_id=f"fork-{tag}",
                task_id="t-conc",
                run_id="r-conc",
                parent_session_id=f"sess-{tag}",
                fork_name=f"fork-{tag}",
            ),
            base_classification=BaseClassification.DISCARDABLE_NOISE,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        ),
        transaction_id=f"tx-{tag}",
        batch_id=f"b-{tag}",
        batch_position=0,
    )
    sentinel = log.stamp_for_batch(
        EventIntent(
            local_intent_id=f"ca-{tag}",
            event_type=EventType.COMMIT_ACCEPTED,
            event_category=EventCategory.COMMIT,
            payload={"target_entity_id": "c-1", "touch_type": "read"},
            provenance_hint=ProvenanceHint(
                actor_type=ActorType.AGENT_FORK.value,
                actor_id=f"fork-{tag}",
                task_id="t-conc",
                run_id="r-conc",
                parent_session_id=f"sess-{tag}",
                fork_name=f"fork-{tag}",
            ),
            base_classification=BaseClassification.DISCARDABLE_NOISE,
            supersede=Supersede(is_supersede=False),
            subjects=[Subject(entity_type=SubjectEntityType.CONCEPT, entity_id="c-1", role=SubjectRole.PRIMARY)],
            schema_version="1.0.0",
        ),
        transaction_id=f"tx-{tag}",
        batch_id=f"b-{tag}",
        batch_position=1,
    )
    log.append_transaction_batch(build_accepted_batch_e11(f"b-{tag}", f"tx-{tag}", [domain], sentinel))


# ─── workers (module-level → picklable under "spawn") ───────────────────────────


def _legacy_stale_counter_writer(
    log_path: str,
    wid: int,
    n_writes: int,
    barrier: Any,
    result_q: Any,
) -> None:
    """Reconstruct the *pre-fix* mechanism: stale in-memory counter, no cross-process lock.

    Each process reads next-seq once at startup (the barrier guarantees the log is still
    empty for everyone, so all capture the same start), then assigns from a local counter
    without re-reading disk and appends raw without any flock. K processes deterministically
    write the same seq range → collisions. Lives entirely in the test (does NOT modify src);
    it exists to prove the harness can detect collisions and that the historical mechanism
    produced them.
    """
    try:
        log = EventLog(Path(log_path))
        start_seq = log.next_sequence  # in-memory; == max_on_disk + 1 (0 on a fresh log)
        active = log.log_path
        records = [
            log._stamp(  # mint a real EventRecord at the chosen seq (old write_direct internals)
                _node_touched_intent(wid, i),
                sequence_number=start_seq + i,
                transaction_id=None,
                batch_id=None,
                batch_position=None,
            )
            for i in range(n_writes)
        ]
        barrier.wait()  # everyone has the same start_seq fixed; now race the un-locked appends
        fd = os.open(active, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            for rec in records:  # single-syscall O_APPEND of a small record → atomic, no corruption
                os.write(fd, (rec.model_dump_json() + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        result_q.put(("ok", wid, n_writes))
    except Exception as exc:
        import traceback

        result_q.put(("err", wid, f"{exc!r}\n{traceback.format_exc()}"))


def _real_barrier_writer(
    log_path: str,
    wid: int,
    n_direct: int,
    n_batches: int,
    barrier: Any,
    result_q: Any,
    *,
    disable_lock: bool = False,
    read_delay_s: float = 0.0,
) -> None:
    """Append via the *real* EventLog write paths from a separate OS process, barrier-synced.

    ``disable_lock`` / ``read_delay_s`` are test instruments applied here (inside the child
    interpreter, so they never leak to the parent or other tests) used only by
    test_flock_is_load_bearing to expose the read→write window when the flock is removed.
    """
    try:
        if disable_lock:
            EventLog._file_lock = lambda self: contextlib.nullcontext()  # type: ignore[method-assign]
        if read_delay_s > 0:
            _orig_read = EventLog._read_max_seq_on_disk

            def _delayed_read(self: EventLog) -> int:
                value = _orig_read(self)
                time.sleep(read_delay_s)  # widen the read→write window deterministically
                return value

            EventLog._read_max_seq_on_disk = _delayed_read  # type: ignore[method-assign]

        log = EventLog(Path(log_path))
        intents = [_node_touched_intent(wid, i) for i in range(n_direct)]
        written = 0
        barrier.wait()  # all workers hammer simultaneously → maximal contention on the lock
        for intent in intents:
            log.write_direct(intent)
            written += 1
        for bidx in range(n_batches):
            _append_one_batch(log, wid, bidx)
            written += 2  # domain + sentinel
        result_q.put(("ok", wid, written))
    except Exception as exc:
        import traceback

        result_q.put(("err", wid, f"{exc!r}\n{traceback.format_exc()}"))


# ─── tests ──────────────────────────────────────────────────────────────────────


def test_legacy_in_memory_counter_reproduces_collision(tmp_path: Path) -> None:
    """Non-vacuity proof: the pre-fix mechanism collides, and this harness detects it.

    Without this, a green no-collision test could merely mean "the processes never actually
    overlapped". Here the documented pre-fix mechanism (stale in-memory counter, no flock) is
    run under the same multi-process harness and MUST produce duplicate sequence_numbers.
    """
    log_path = tmp_path / "events.log"
    ctx = multiprocessing.get_context("spawn")
    n_workers, n_writes = 5, 8
    with ctx.Manager() as mgr:
        barrier = mgr.Barrier(n_workers)
        result_q = mgr.Queue()
        procs = [
            ctx.Process(
                target=_legacy_stale_counter_writer,
                args=(str(log_path), wid, n_writes, barrier, result_q),
            )
            for wid in range(n_workers)
        ]
        for p in procs:
            p.start()
        _join_and_assert_no_worker_errors(procs, result_q, n_workers)
        seqs = _all_segment_seqs(log_path)

    assert len(seqs) == n_workers * n_writes  # every line landed (atomic O_APPEND, no corruption)
    assert len(set(seqs)) < len(seqs), "expected the pre-fix stale-counter mechanism to collide"
    # Each process wrote the identical [start .. start+n_writes) range → n_writes distinct seqs.
    assert len(set(seqs)) == n_writes


@pytest.mark.invariant
def test_concurrent_path_a_batches_never_share_a_sequence_number(tmp_path: Path) -> None:
    """The fix also covers path A: concurrent append_transaction_batch from many processes.

    append_transaction_batch re-derives the authoritative seq from the on-disk tail under the
    flock; concurrent batches (multi-record) from independent processes must still yield unique,
    gap-free sequence_numbers.
    """
    log_path = tmp_path / "events.log"
    ctx = multiprocessing.get_context("spawn")
    n_workers, n_batches = 5, 4
    expected_total = n_workers * n_batches * 2  # each batch = domain + sentinel
    with ctx.Manager() as mgr:
        barrier = mgr.Barrier(n_workers)
        result_q = mgr.Queue()
        procs = [
            ctx.Process(
                target=_real_barrier_writer,
                args=(str(log_path), wid, 0, n_batches, barrier, result_q),
            )
            for wid in range(n_workers)
        ]
        for p in procs:
            p.start()
        _join_and_assert_no_worker_errors(procs, result_q, n_workers)
        seqs = _all_segment_seqs(log_path)

    assert len(seqs) == expected_total
    assert len(set(seqs)) == expected_total, (
        f"path-A sequence_number collision: {len(seqs) - len(set(seqs))} duplicate(s)"
    )
    assert sorted(seqs) == list(range(expected_total))


@pytest.mark.invariant
def test_flock_is_load_bearing(tmp_path: Path) -> None:
    """A/B on the *current* code: the flock — not the disk-rederive alone — closes the window.

    Same real write_direct path, same deterministic read→write delay, run twice:
      * lock removed → every process reads the same on-disk max, then writes it → collision.
      * lock kept    → the 2nd process blocks on flock until the 1st writes+releases, re-reads
                       the advanced max → no collision.
    Proves removing only the flock reintroduces F-019-12, so the flock is load-bearing (the
    disk-rederive without the lock is a TOCTOU that still collides).
    """
    ctx = multiprocessing.get_context("spawn")
    n_workers, n_direct, read_delay_s = 4, 3, 0.05

    def _run(*, disable_lock: bool, dirname: str) -> list[int]:
        log_path = tmp_path / dirname / "events.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with ctx.Manager() as mgr:
            barrier = mgr.Barrier(n_workers)
            result_q = mgr.Queue()
            procs = [
                ctx.Process(
                    target=_real_barrier_writer,
                    args=(str(log_path), wid, n_direct, 0, barrier, result_q),
                    kwargs={"disable_lock": disable_lock, "read_delay_s": read_delay_s},
                )
                for wid in range(n_workers)
            ]
            for p in procs:
                p.start()
            _join_and_assert_no_worker_errors(procs, result_q, n_workers)
            return _all_segment_seqs(log_path)

    seqs_no_lock = _run(disable_lock=True, dirname="no_lock")
    assert len(set(seqs_no_lock)) < len(seqs_no_lock), (
        "removing the flock should reintroduce sequence_number collisions (F-019-12)"
    )

    seqs_locked = _run(disable_lock=False, dirname="locked")
    assert len(set(seqs_locked)) == len(seqs_locked), (
        "with the flock held, the read→write window must not collide even under a delay"
    )
    assert sorted(seqs_locked) == list(range(n_workers * n_direct))


@pytest.mark.invariant
def test_lock_key_is_path_normalized_for_aliased_log(tmp_path: Path) -> None:
    """The lock *key* is normalized so different spellings of the same physical log share a lock.

    F-019-12 hardening: the flock only excludes two processes when they open the *same* lock
    file. The lock path is derived from the log path's parent, so two processes that name the
    same physical events.log via different spellings — a ``..`` redundant segment, or a symlinked
    directory (the real ``../.towow`` vs ``./.towow`` foot-gun) — must still resolve to one lock
    file. Otherwise distinct path strings → distinct lock files → no mutual exclusion → the
    sequence_number collision returns. This asserts the normalization directly (zero mock, real
    filesystem): the same physical log reached three ways yields one identical ``_lock_path``.
    """
    real_dir = tmp_path / "dot_towow"
    real_dir.mkdir()
    canonical_log = real_dir / "events.log"

    # (a) canonical absolute spelling.
    log_canonical = EventLog(canonical_log)

    # (b) same file reached through a redundant ``..`` segment (…/dot_towow/sub/../events.log).
    sub = real_dir / "sub"
    sub.mkdir()
    log_via_dotdot = EventLog(sub / ".." / "events.log")

    # (c) same file reached through a symlinked directory alias (mirrors ../.towow vs ./.towow).
    alias_dir = tmp_path / "alias_towow"
    alias_dir.symlink_to(real_dir, target_is_directory=True)
    log_via_symlink = EventLog(alias_dir / "events.log")

    # All three name the identical physical log → identical normalized lock key.
    assert log_via_dotdot._lock_path == log_canonical._lock_path
    assert log_via_symlink._lock_path == log_canonical._lock_path
    # And it is the resolved canonical location (no ``..`` / symlink left in the key).
    assert log_canonical._lock_path == real_dir.resolve() / "locks" / "events.log.lock"
    # Sanity: the three EventLogs really did open the one physical file (not coincidental keys).
    assert canonical_log.samefile(sub / ".." / "events.log")
    assert canonical_log.samefile(alias_dir / "events.log")
