from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .models import ClaimRecord, DriftFinding, MechanismCard, UnknownRecord

STRONG_EVIDENCE = {"code", "runtime", "test", "event"}
VALID_STATUSES = {
    "legacy",
    "current_verified",
    "experimental",
    "designed_target",
    "blocked",
    "unknown",
    "written_only",
}
VALID_EVIDENCE_KINDS = {
    "code",
    "runtime",
    "test",
    "event",
    "schema",
    "commit",
    "transcript",
    "document",
    "external",
}
DIRECT_TRACE_FIELDS = (
    "function_definitions",
    "callers",
    "consumers",
    "test_body_observations",
    "execution_event_runtime_observations",
)


class ValidationError(ValueError):
    pass


def _independent_groups(items: Iterable[Any]) -> set[str]:
    return {item.independent_group for item in items if item.independent_group}


def _validate_evidence(items: Iterable[Any], owner: str) -> None:
    seen: set[str] = set()
    for item in items:
        if not item.evidence_id or item.evidence_id in seen:
            raise ValidationError(f"{owner} has missing or duplicate evidence_id")
        seen.add(item.evidence_id)
        if item.kind not in VALID_EVIDENCE_KINDS:
            raise ValidationError(f"{owner} has unsupported evidence kind")
        if not item.locator or not item.source_snapshot_id or not item.content_hash:
            raise ValidationError(f"{owner} evidence lacks immutable provenance")
        if not item.captured_at or not item.independent_group:
            raise ValidationError(f"{owner} evidence lacks capture/group metadata")


def validate_claim(claim: ClaimRecord) -> None:
    if not claim.claim_id or not claim.text.strip():
        raise ValidationError("claim_id and text are required")
    if not claim.scope.strip() or not claim.baseline.strip():
        raise ValidationError("claim scope and baseline are required")
    if not claim.success_criteria.strip():
        raise ValidationError("claim success_criteria is required")
    if claim.status not in VALID_STATUSES:
        raise ValidationError(f"unsupported claim status: {claim.status}")
    _validate_evidence(claim.evidence, claim.claim_id)
    if claim.status == "current_verified":
        if len(_independent_groups(claim.evidence)) < 2:
            raise ValidationError(
                "current_verified claim requires two independent evidence groups"
            )
        if not any(item.kind in STRONG_EVIDENCE for item in claim.evidence):
            raise ValidationError(
                "current_verified claim requires code/runtime/test/event evidence"
            )
        if not claim.last_verified_at:
            raise ValidationError(
                "current_verified claim requires last_verified_at"
            )
    if claim.status in {"designed_target", "written_only"} and claim.last_verified_at:
        raise ValidationError(
            f"{claim.status} claim cannot carry runtime verification timestamp"
        )


def validate_mechanism(card: MechanismCard) -> None:
    if not card.mechanism_id or not card.public_name.strip():
        raise ValidationError("mechanism_id and public_name are required")
    if card.status not in VALID_STATUSES:
        raise ValidationError(f"unsupported mechanism status: {card.status}")
    _validate_evidence(card.evidence, card.mechanism_id)
    if len(card.inventory_item_ids) != len(set(card.inventory_item_ids)):
        raise ValidationError("mechanism inventory_item_ids must be unique")
    if card.status == "current_verified":
        if not isinstance(card.verification_trace, dict):
            missing_trace = list(DIRECT_TRACE_FIELDS)
        else:
            missing_trace = [
                field
                for field in DIRECT_TRACE_FIELDS
                if not isinstance(card.verification_trace.get(field), list)
                or not card.verification_trace[field]
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in card.verification_trace[field]
                )
            ]
        if missing_trace:
            raise ValidationError(
                "current_verified mechanism requires direct-source "
                f"verification_trace fields: {', '.join(missing_trace)}"
            )
        if len(_independent_groups(card.evidence)) < 2:
            raise ValidationError(
                "current_verified mechanism requires two independent evidence groups"
            )
        if not any(item.kind in STRONG_EVIDENCE for item in card.evidence):
            raise ValidationError(
                "current_verified mechanism requires executable evidence"
            )
    if card.status == "unknown" and not card.unresolved_questions:
        raise ValidationError("unknown mechanism requires unresolved_questions")
    if not card.failure_modes:
        raise ValidationError("mechanism must name at least one failure mode")
    if card.status == "current_verified" and not card.inventory_item_ids:
        raise ValidationError(
            "current_verified mechanism must cover inventory items"
        )


def validate_unknown(record: UnknownRecord) -> None:
    if not record.unknown_id or not record.inventory_item_id:
        raise ValidationError("unknown_id and inventory_item_id are required")
    if not record.object_type.strip() or not record.locator.strip():
        raise ValidationError("unknown object_type and locator are required")
    if not record.question.strip() or not record.next_check.strip():
        raise ValidationError("unknown question and next_check are required")
    _validate_evidence(record.evidence, record.unknown_id)


def validate_drift(finding: DriftFinding) -> None:
    if not finding.drift_id:
        raise ValidationError("drift_id is required")
    if not finding.surface_from.strip() or not finding.surface_to.strip():
        raise ValidationError("drift surfaces are required")
    if not finding.source_locators:
        raise ValidationError("drift requires source_locators")
    if finding.severity not in {"critical", "high", "medium", "low"}:
        raise ValidationError(f"unsupported drift severity: {finding.severity}")
    if finding.state not in {"open", "accepted", "resolved", "disputed"}:
        raise ValidationError(f"unsupported drift state: {finding.state}")
    if not finding.remediation_or_downgrade.strip():
        raise ValidationError("drift remediation_or_downgrade is required")
    _validate_evidence(finding.evidence, finding.drift_id)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def atomic_create_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp_name, path)
        except FileExistsError as exc:
            raise ValidationError(f"refusing to overwrite existing file: {path}") from exc
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class RegistryStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _safe_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValidationError("registry path escapes root")
        return candidate

    def write_mechanisms(self, cards: list[MechanismCard]) -> Path:
        if len({card.mechanism_id for card in cards}) != len(cards):
            raise ValidationError("mechanism_id values must be unique")
        for card in cards:
            validate_mechanism(card)
        target = self._safe_path("registries/mechanisms.json")
        atomic_write_json(target, [card.to_dict() for card in cards])
        return target

    def write_claims(self, claims: list[ClaimRecord]) -> Path:
        if len({claim.claim_id for claim in claims}) != len(claims):
            raise ValidationError("claim_id values must be unique")
        for claim in claims:
            validate_claim(claim)
        target = self._safe_path("registries/claims.json")
        atomic_write_json(target, [claim.to_dict() for claim in claims])
        return target
