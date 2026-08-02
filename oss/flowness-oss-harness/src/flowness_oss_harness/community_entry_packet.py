from __future__ import annotations

"""Fail-closed verification for private community-entry packet material.

The packet preserves a useful distinction: private templates can make future
review questions visible, but cannot create a public contact route, promise
support, accept a contribution, or clear an export/owner gate.
"""

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from .integrity import verify_self_hash
from .registry import ValidationError


SCHEMA_VERSION = "community-entry-packet/v1"
PRIVATE_STATUS = "private_candidate_only"
REQUIRED_SOURCE_IDS = {
    "rights_export_contract",
    "readiness_roadmap",
    "round_2_community_jury",
    "public_package_boundary",
}
REQUIRED_ARTIFACT_IDS = {"entry_packet", "route_templates"}
REQUIRED_SURFACE_IDS = {
    "security_route",
    "bug_issue_route",
    "feature_request_route",
    "support_route",
    "roadmap_triage_route",
    "wow_legacy_route",
}
REQUIRED_BLOCKERS = {
    "DRIFT-PUBLIC-EXPORT-004",
    "UNKNOWN-DX-INDEPENDENT-CLEAN-ROOM-001",
    "UNKNOWN-SERVER-RUNTIME-001",
    "UNKNOWN-WOW-CONTINUITY-001",
    "R1-SUCCESSOR-WHOLE-JURY-REQUIRED",
    "OWNER-RELEASE-AUTHORIZATION-REQUIRED",
}


def _safe_file(root: Path, relative: str, error: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValidationError(error)
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure) in {"", "."}:
        raise ValidationError(error)
    path = root.joinpath(*pure.parts)
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValidationError(error)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValidationError(error)
    if not path.is_file() or path.is_symlink():
        raise ValidationError(error)
    return path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_bound_files(
    root: Path, bindings: Any, *, expected_ids: set[str], field: str
) -> dict[str, dict[str, str]]:
    if not isinstance(bindings, list) or not bindings:
        raise ValidationError(f"COMMUNITY-ENTRY-{field.upper()}-INVALID")
    by_id: dict[str, dict[str, str]] = {}
    for item in bindings:
        if not isinstance(item, dict) or set(item) != {"binding_id", "path", "sha256"}:
            raise ValidationError(f"COMMUNITY-ENTRY-{field.upper()}-INVALID")
        binding_id, relative, expected_hash = item.values()
        if not isinstance(binding_id, str) or binding_id in by_id:
            raise ValidationError(f"COMMUNITY-ENTRY-{field.upper()}-INVALID")
        if not isinstance(expected_hash, str) or not expected_hash.startswith("sha256:") or len(expected_hash) != 71:
            raise ValidationError(f"COMMUNITY-ENTRY-{field.upper()}-INVALID")
        path = _safe_file(root, relative, f"COMMUNITY-ENTRY-{field.upper()}-PATH-INVALID")
        if _sha256(path) != expected_hash:
            raise ValidationError(f"COMMUNITY-ENTRY-{field.upper()}-HASH-MISMATCH:{binding_id}")
        by_id[binding_id] = {"path": relative, "sha256": expected_hash}
    if set(by_id) != expected_ids:
        raise ValidationError(f"COMMUNITY-ENTRY-{field.upper()}-COVERAGE-INCOMPLETE")
    return by_id


def validate_community_entry_packet(packet: dict[str, Any], root: Path | str) -> dict[str, Any]:
    """Validate private packet bytes and keep every outward route closed."""

    expected = {
        "schema_version", "packet_id", "scope", "candidate", "source_bindings",
        "artifact_bindings", "claim_status_bindings", "authorization", "packet_hash",
    }
    if not isinstance(packet, dict) or set(packet) != expected or packet.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("COMMUNITY-ENTRY-PACKET-INVALID")
    if packet["scope"] != "private_staging_only":
        raise ValidationError("COMMUNITY-ENTRY-SCOPE-INVALID")
    if packet["authorization"] != "not_authorized":
        raise ValidationError("COMMUNITY-ENTRY-AUTHORIZATION-INVALID")
    if not isinstance(packet["packet_id"], str) or not packet["packet_id"]:
        raise ValidationError("COMMUNITY-ENTRY-PACKET-INVALID")
    candidate = packet["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != {"candidate_id", "status"}:
        raise ValidationError("COMMUNITY-ENTRY-CANDIDATE-INVALID")
    if not isinstance(candidate["candidate_id"], str) or not candidate["candidate_id"] or candidate["status"] != PRIVATE_STATUS:
        raise ValidationError("COMMUNITY-ENTRY-CANDIDATE-INVALID")
    verify_self_hash(packet, "packet_hash")

    base = Path(root).resolve(strict=True)
    if base.is_symlink() or not base.is_dir():
        raise ValidationError("COMMUNITY-ENTRY-ROOT-INVALID")
    _validate_bound_files(base, packet["source_bindings"], expected_ids=REQUIRED_SOURCE_IDS, field="source")
    _validate_bound_files(base, packet["artifact_bindings"], expected_ids=REQUIRED_ARTIFACT_IDS, field="artifact")

    claims = packet["claim_status_bindings"]
    if not isinstance(claims, list) or len(claims) != len(REQUIRED_SURFACE_IDS):
        raise ValidationError("COMMUNITY-ENTRY-CLAIM-BINDINGS-INVALID")
    seen: set[str] = set()
    for claim in claims:
        required_claim = {"surface_id", "claim_id", "status", "evidence_status", "blocker_ids", "artifact_id"}
        if not isinstance(claim, dict) or set(claim) != required_claim:
            raise ValidationError("COMMUNITY-ENTRY-CLAIM-BINDINGS-INVALID")
        surface_id = claim["surface_id"]
        if not isinstance(surface_id, str) or surface_id in seen:
            raise ValidationError("COMMUNITY-ENTRY-CLAIM-BINDINGS-INVALID")
        seen.add(surface_id)
        if (
            not isinstance(claim["claim_id"], str)
            or claim["status"] != PRIVATE_STATUS
            or claim["evidence_status"] != "unsealed_local_text"
            or claim["artifact_id"] not in REQUIRED_ARTIFACT_IDS
            or not isinstance(claim["blocker_ids"], list)
            or not REQUIRED_BLOCKERS.issubset(set(claim["blocker_ids"]))
        ):
            raise ValidationError(f"COMMUNITY-ENTRY-CLAIM-STATUS-INVALID:{surface_id}")
    if seen != REQUIRED_SURFACE_IDS:
        raise ValidationError("COMMUNITY-ENTRY-CLAIM-COVERAGE-INCOMPLETE")

    return {
        "schema_version": "community-entry-packet-report/v1",
        "packet_id": packet["packet_id"],
        "candidate_id": candidate["candidate_id"],
        "state": "private_entry_only",
        "eligible_for_public_community_entry": False,
        "blocked_surface_ids": sorted(seen),
        "blocker_ids": sorted(REQUIRED_BLOCKERS),
        "boundary": (
            "This byte-bound private packet does not create a contact route, accept a contribution, "
            "make a support/security/roadmap commitment, clear rights, or authorize publication."
        ),
    }
