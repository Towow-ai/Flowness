from __future__ import annotations

"""Verify the private Ledger candidate's product-package surface.

This is intentionally a *staging inventory*, not a package assembler.  It
binds the useful candidate materials that already exist to exact bytes and
keeps the absent public-release material explicit.  It must never be used to
create legal text, infer distribution rights, or promote the candidate.
"""

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from .integrity import verify_self_hash
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


SCHEMA = SCHEMAS_ROOT / "candidate-package-surface.schema.json"
SCHEMA_VERSION = "candidate-package-surface/v1"
PRIVATE_STATUS = "private_candidate_only"
EXPECTED_PACKAGE = "flowness-ledger-core"

# These are all actual candidate materials.  A release gate must not quietly
# substitute one of them for public license, support, security, or clean-room
# evidence.
REQUIRED_SURFACE_IDS = {
    "candidate_readme",
    "candidate_quickstart",
    "offline_wheel_builder",
    "offline_install_attestor",
    "candidate_compatibility",
    "architecture_d0_d2",
    "architecture_d3_d5",
    "casebook",
    "technical_report",
    "faq_en",
    "faq_zh_cn",
    "demo_scenario_pack",
    "local_measurement_summary",
}

# These mirror the file-backed Alpha requirements.  They are purposefully
# represented as gaps while the export/right and independent evidence gates
# are absent; this inventory has no authority to scaffold them.
REQUIRED_RELEASE_GAP_IDS = {
    "license-matrix",
    "notice",
    "sbom-spdx",
    "security",
    "contributing",
    "support",
    "migration-guide",
    "release-notes",
    "source-allowlist",
    "cleanroom",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-PATH-INVALID")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or str(pure) in {"", "."}:
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-PATH-INVALID")
    path = root.joinpath(*pure.parts)
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValidationError("CANDIDATE-PACKAGE-SURFACE-PATH-INVALID")
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-PATH-INVALID")
    if path.is_symlink() or not path.is_file():
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-FILE-MISSING")
    return path


def validate_private_candidate_package_surface(payload: dict[str, Any], root: Path | str) -> dict[str, Any]:
    """Validate exact private candidate bytes and preserve every release gap."""

    validate_payload(payload, SCHEMA, "Candidate package surface")
    verify_self_hash(payload, "surface_hash")
    if (
        payload["schema_version"] != SCHEMA_VERSION
        or payload["scope"] != "private_staging_only"
        or payload["authorization"] != "not_authorized"
        or payload["candidate_package"] != EXPECTED_PACKAGE
        or payload["candidate_status"] != PRIVATE_STATUS
        or payload["release_package_eligible"] is not False
    ):
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-IDENTITY-INVALID")

    base = Path(root).resolve(strict=True)
    if base.is_symlink() or not base.is_dir() or base.name != EXPECTED_PACKAGE:
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-ROOT-INVALID")

    seen: set[str] = set()
    surfaces: list[dict[str, str]] = []
    for item in payload["surface_bindings"]:
        surface_id = item["surface_id"]
        if surface_id in seen:
            raise ValidationError("CANDIDATE-PACKAGE-SURFACE-DUPLICATE")
        seen.add(surface_id)
        path = _safe_file(base, item["path"])
        observed = _sha256(path)
        if observed != item["sha256"]:
            raise ValidationError(f"CANDIDATE-PACKAGE-SURFACE-HASH-MISMATCH:{surface_id}")
        surfaces.append({"surface_id": surface_id, "path": item["path"], "sha256": observed})
    if seen != REQUIRED_SURFACE_IDS:
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-COVERAGE-INCOMPLETE")

    gaps = payload["release_requirement_gaps"]
    gap_ids = [item["requirement_id"] for item in gaps]
    if len(gap_ids) != len(set(gap_ids)) or set(gap_ids) != REQUIRED_RELEASE_GAP_IDS:
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-RELEASE-GAPS-INCOMPLETE")
    if any(item["state"] != "not_created_pending_authorized_export" for item in gaps):
        raise ValidationError("CANDIDATE-PACKAGE-SURFACE-RELEASE-GAP-PROMOTED")

    return {
        "schema_version": "candidate-package-surface-report/v1",
        "surface_id": payload["surface_id"],
        "candidate_package": EXPECTED_PACKAGE,
        "state": PRIVATE_STATUS,
        "candidate_surface_count": len(surfaces),
        "bound_surfaces": sorted(surfaces, key=lambda item: item["surface_id"]),
        "release_package_eligible": False,
        "blocked_requirement_ids": sorted(gap_ids),
        "boundary": (
            "This verifies private candidate materials and their byte identities only. It does not create or approve a LICENSE, NOTICE, SBOM, SECURITY, CONTRIBUTING, SUPPORT, migration guide, release notes, source allowlist, clean-room result, export, release, or publication."
        ),
    }
