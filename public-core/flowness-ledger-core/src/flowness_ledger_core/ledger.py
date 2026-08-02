from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


class LedgerError(ValueError):
    """The on-disk history cannot satisfy the candidate contract."""


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class RecoveryReport:
    """A self-authenticating record of one completed local recovery.

    ``report_hash`` is deliberately derived by :meth:`to_dict`, rather than
    stored as an input.  This makes the persisted bytes independently
    checkable without trusting a caller's claimed hash.
    """

    stream: str
    action: str
    affected_bytes: int
    pending_proposals: tuple[str, ...]
    committed_watermark: int
    ledger_head_sequence: int
    ledger_head_hash: str | None

    FORMAT = "flowness-ledger-core/recovery-report/v1"

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "format": self.FORMAT,
            "ledger_format": Ledger.FORMAT,
            **asdict(self),
        }
        payload["report_hash"] = _sha(payload)
        return payload


class Ledger:
    """POSIX-local JSONL ledger with proposal visibility sentinels.

    The caller owns all payload semantics. This class only protects ordering,
    immutability, terminal-decision consistency and committed visibility.
    """

    FORMAT = "flowness-ledger-core/v0"

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory).resolve()
        self.path = self.directory / "ledger.jsonl"
        self.lock_path = self.directory / "ledger.lock"
        self.reports_directory = self.directory / "recovery-reports"

    @classmethod
    def open(cls, directory: Path | str, *, create: bool = False) -> "Ledger":
        ledger = cls(directory)
        if create:
            ledger.directory.mkdir(parents=True, exist_ok=True)
            if ledger.directory.is_symlink() or not ledger.directory.is_dir():
                raise LedgerError("ledger directory is not a regular directory")
            ledger.path.touch(exist_ok=True)
        if not ledger.path.is_file() or ledger.path.is_symlink():
            raise LedgerError("ledger stream does not exist or is unsafe")
        return ledger

    def _lock(self) -> int:
        if self.lock_path.is_symlink():
            raise LedgerError("ledger lock is unsafe")
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    @staticmethod
    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def _load(self) -> tuple[list[dict[str, Any]], int]:
        raw = self.path.read_bytes()
        incomplete_bytes = 0
        complete = raw
        if raw and not raw.endswith(b"\n"):
            cut = raw.rfind(b"\n") + 1
            complete, incomplete_bytes = raw[:cut], len(raw) - cut
        rows: list[dict[str, Any]] = []
        previous: str | None = None
        for index, line in enumerate(complete.splitlines(), 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"invalid complete record at line {index}") from exc
            if not isinstance(row, dict):
                raise LedgerError(f"record at line {index} is not an object")
            unsigned = {key: value for key, value in row.items() if key != "record_hash"}
            if (
                row.get("format") != self.FORMAT
                or row.get("sequence") != index
                or row.get("previous_record_hash") != previous
                or row.get("record_hash") != _sha(unsigned)
                or not isinstance(row.get("record_id"), str)
            ):
                raise LedgerError(f"invalid immutable record at line {index}")
            previous = row["record_hash"]
            rows.append(row)
        if len({row["record_id"] for row in rows}) != len(rows):
            raise LedgerError("duplicate record_id")
        return rows, incomplete_bytes

    @staticmethod
    def _index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        proposals: dict[str, dict[str, Any]] = {}
        for row in rows:
            proposal_id = row.get("proposal_id")
            if row["kind"] == "proposal":
                if not isinstance(proposal_id, str) or proposal_id in proposals:
                    raise LedgerError("invalid or duplicate proposal")
                proposals[proposal_id] = {"begin": row, "records": [], "decision": None}
            elif row["kind"] == "proposed_record":
                if proposal_id not in proposals or proposals[proposal_id]["decision"] is not None:
                    raise LedgerError("record outside a pending proposal")
                proposals[proposal_id]["records"].append(row)
            elif row["kind"] == "decision":
                if proposal_id not in proposals or proposals[proposal_id]["decision"] is not None:
                    raise LedgerError("invalid decision")
                if row.get("outcome") not in {"accepted", "rejected"}:
                    raise LedgerError("invalid decision outcome")
                proposals[proposal_id]["decision"] = row
            else:
                raise LedgerError("unknown record kind")
        return proposals

    def _append(self, kind: str, *, proposal_id: str, payload: dict[str, Any], outcome: str | None = None) -> dict[str, Any]:
        fd = self._lock()
        try:
            rows, incomplete = self._load()
            if incomplete:
                raise LedgerError("run recover before appending after an incomplete tail")
            self._index(rows)
            row = {
                "format": self.FORMAT,
                "record_id": hashlib.sha256(os.urandom(32)).hexdigest(),
                "sequence": len(rows) + 1,
                "kind": kind,
                "proposal_id": proposal_id,
                "occurred_at": _now(),
                "payload": payload,
                "outcome": outcome,
                "previous_record_hash": rows[-1]["record_hash"] if rows else None,
            }
            row["record_hash"] = _sha(row)
            encoded = _canonical(row) + b"\n"
            with self.path.open("ab", buffering=0) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            return row
        finally:
            self._unlock(fd)

    def begin_proposal(self, proposal_id: str, metadata: dict[str, Any], evidence_refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not proposal_id:
            raise LedgerError("proposal_id is required")
        rows, incomplete = self._load()
        if incomplete:
            raise LedgerError("run recover before beginning a proposal")
        proposals = self._index(rows)
        payload = {"metadata": metadata, "evidence_refs": evidence_refs or []}
        existing = proposals.get(proposal_id)
        if existing is not None:
            if existing["begin"]["payload"] == payload:
                return existing["begin"]
            raise LedgerError("proposal_id is already bound to different immutable input")
        return self._append("proposal", proposal_id=proposal_id, payload=payload)

    def append_proposed(self, proposal_id: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not records:
            raise LedgerError("at least one proposed record is required")
        rows, incomplete = self._load()
        if incomplete:
            raise LedgerError("run recover before appending proposed records")
        proposals = self._index(rows)
        if proposal_id not in proposals or proposals[proposal_id]["decision"] is not None:
            raise LedgerError("proposal is not pending")
        return [self._append("proposed_record", proposal_id=proposal_id, payload=record) for record in records]

    def decide(self, proposal_id: str, outcome: str, metadata: dict[str, Any], evidence_refs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if outcome not in {"accepted", "rejected"}:
            raise LedgerError("outcome must be accepted or rejected")
        rows, incomplete = self._load()
        if incomplete:
            raise LedgerError("run recover before deciding")
        proposals = self._index(rows)
        if proposal_id not in proposals:
            raise LedgerError("unknown proposal")
        payload = {"metadata": metadata, "evidence_refs": evidence_refs or []}
        existing = proposals[proposal_id]["decision"]
        if existing is not None:
            if existing["outcome"] == outcome and existing["payload"] == payload:
                return existing
            raise LedgerError("conflicting immutable decision")
        return self._append("decision", proposal_id=proposal_id, payload=payload, outcome=outcome)

    def read(self, view: str = "committed") -> list[dict[str, Any]]:
        if view not in {"audit", "committed"}:
            raise LedgerError("view must be audit or committed")
        rows, incomplete = self._load()
        if incomplete:
            raise LedgerError("incomplete tail requires recover before reading")
        proposals = self._index(rows)
        if view == "audit":
            return rows
        accepted = {key for key, value in proposals.items() if value["decision"] and value["decision"]["outcome"] == "accepted"}
        return [row for row in rows if row["kind"] == "proposed_record" and row["proposal_id"] in accepted]

    def recovery_report_path(self, report: RecoveryReport) -> Path:
        """Return the deterministic immutable path for a verified report."""

        report_hash = report.to_dict()["report_hash"]
        return self.reports_directory / f"{report_hash.removeprefix('sha256:')}.json"

    def _persist_recovery_report(self, report: RecoveryReport) -> Path:
        """Persist a report once, without making an existing report mutable."""

        if self.reports_directory.exists():
            if self.reports_directory.is_symlink() or not self.reports_directory.is_dir():
                raise LedgerError("recovery report directory is unsafe")
        else:
            self.reports_directory.mkdir(mode=0o700)

        destination = self.recovery_report_path(report)
        if destination.exists():
            if destination.is_symlink() or not destination.is_file():
                raise LedgerError("recovery report path is unsafe")
            if destination.read_bytes() != _canonical(report.to_dict()) + b"\n":
                raise LedgerError("immutable recovery report hash collision")
            return destination

        encoded = _canonical(report.to_dict()) + b"\n"
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # A concurrent process may have completed the same immutable write
            # after the exists check.  Re-enter through the exact-byte check.
            return self._persist_recovery_report(report)
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(fd, encoded[offset:])
            os.fsync(fd)
        finally:
            os.close(fd)
        directory_fd = os.open(self.reports_directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return destination

    def verify_recovery_report(self, report_path: Path | str) -> dict[str, Any]:
        """Verify a persisted report and its hash-bound ledger prefix.

        A later append does not invalidate an earlier recovery receipt: the
        receipt is checked against the exact immutable ledger prefix it names.
        It does *not* assert that the report describes the current ledger head.
        """

        path = Path(report_path)
        try:
            resolved_reports = self.reports_directory.resolve(strict=True)
            resolved_path = path.resolve(strict=True)
            resolved_path.relative_to(resolved_reports)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise LedgerError("recovery report is outside the safe report directory") from exc
        if path.is_symlink() or not path.is_file():
            raise LedgerError("recovery report path is unsafe")
        try:
            report = json.loads(path.read_bytes())
        except json.JSONDecodeError as exc:
            raise LedgerError("recovery report is not valid JSON") from exc
        if not isinstance(report, dict):
            raise LedgerError("recovery report is not an object")
        report_hash = report.get("report_hash")
        unsigned = {key: value for key, value in report.items() if key != "report_hash"}
        if (
            report.get("format") != RecoveryReport.FORMAT
            or report.get("ledger_format") != self.FORMAT
            or not isinstance(report_hash, str)
            or report_hash != _sha(unsigned)
        ):
            raise LedgerError("invalid self-hashed recovery report")
        if report.get("stream") != "ledger.jsonl":
            raise LedgerError("recovery report names an unexpected stream")
        head_sequence = report.get("ledger_head_sequence")
        head_hash = report.get("ledger_head_hash")
        if not isinstance(head_sequence, int) or head_sequence < 0:
            raise LedgerError("recovery report has invalid ledger head sequence")
        if head_sequence == 0 and head_hash is not None:
            raise LedgerError("empty ledger recovery report has a head hash")
        if head_sequence > 0 and not isinstance(head_hash, str):
            raise LedgerError("nonempty ledger recovery report has no head hash")

        rows, incomplete = self._load()
        if incomplete:
            raise LedgerError("current ledger has an incomplete tail")
        if head_sequence > len(rows):
            raise LedgerError("recovery report names a future ledger head")
        prefix = rows[:head_sequence]
        actual_head_hash = prefix[-1]["record_hash"] if prefix else None
        if actual_head_hash != head_hash:
            raise LedgerError("recovery report ledger head hash does not match")
        proposals = self._index(prefix)
        pending = sorted(key for key, value in proposals.items() if value["decision"] is None)
        accepted = {
            key
            for key, value in proposals.items()
            if value["decision"] and value["decision"]["outcome"] == "accepted"
        }
        committed = [
            row
            for row in prefix
            if row["kind"] == "proposed_record" and row["proposal_id"] in accepted
        ]
        if (
            report.get("pending_proposals") != pending
            or report.get("committed_watermark")
            != (committed[-1]["sequence"] if committed else 0)
        ):
            raise LedgerError("recovery report state summary does not match ledger prefix")
        return report

    def recover(self, *, persist_report: bool = False) -> RecoveryReport:
        fd = self._lock()
        try:
            rows, incomplete = self._load()
            if incomplete:
                raw = self.path.read_bytes()
                with self.path.open("r+b", buffering=0) as handle:
                    handle.truncate(len(raw) - incomplete)
                    handle.flush()
                    os.fsync(handle.fileno())
                rows, remaining = self._load()
                if remaining:
                    raise LedgerError("tail recovery did not converge")
                action = "truncated_incomplete_tail"
            else:
                action = "no_physical_tail_change"
            proposals = self._index(rows)
            pending = tuple(sorted(key for key, value in proposals.items() if value["decision"] is None))
            committed = self.read("committed")
            report = RecoveryReport(
                "ledger.jsonl",
                action,
                incomplete,
                pending,
                committed[-1]["sequence"] if committed else 0,
                rows[-1]["sequence"] if rows else 0,
                rows[-1]["record_hash"] if rows else None,
            )
            if persist_report:
                self._persist_recovery_report(report)
            return report
        finally:
            self._unlock(fd)
