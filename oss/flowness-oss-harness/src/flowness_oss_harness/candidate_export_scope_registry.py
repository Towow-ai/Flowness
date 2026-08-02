from __future__ import annotations

"""Hash-bound *proposals* for the public Ledger candidate's file scope.

This module is deliberately upstream of every export and rights decision.  It
only enumerates regular files already tracked in the selected Git subtree and
assigns a reviewable proposed disposition.  Every proposal retains empty
origin/rights evidence slots.  Consequently it cannot serve as an allowlist,
sealed export, SPDX/SBOM, rights record, or release approval.
"""

import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


SCHEMA_VERSION = "candidate-export-scope-registry/v1"
SCHEMA = SCHEMAS_ROOT / "candidate-export-scope-registry.schema.json"
_BOUNDARY = (
    "private-staging proposal registry only; it does not decide origin, ownership, licensing, attribution, "
    "or exportability; it does not create an allowlist, sealed export, SPDX/SBOM/NOTICE, release, or publication"
)
_STATES = {"include_proposed", "hold", "exclude"}


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        text=text,
    )
    if completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise ValidationError(stderr.strip() or "EXPORT-SCOPE-GIT-FAILED")
    return completed.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValidationError("EXPORT-SCOPE-ROOT-ESCAPES-REPOSITORY") from exc
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValidationError("EXPORT-SCOPE-PATH-INVALID")
    return relative


def _repo_and_scope(repo: Path, scope_root: Path) -> tuple[Path, str, Path]:
    repo_root = Path(str(_git(repo, "rev-parse", "--show-toplevel")).strip()).resolve(strict=True)
    resolved_scope = scope_root.resolve(strict=True)
    if scope_root.is_symlink() or not resolved_scope.is_dir():
        raise ValidationError("EXPORT-SCOPE-ROOT-UNSAFE")
    relative = _relative(repo_root, resolved_scope)
    return repo_root, relative, resolved_scope


def _assert_clean_scope(repo_root: Path, scope: str) -> None:
    # A proposal is for tracked bytes only.  Staged or unstaged changes make the
    # relationship between the Git tree and the current worktree ambiguous.
    dirty = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=no",
        "--",
        scope,
        text=False,
    )
    assert isinstance(dirty, bytes)
    if dirty:
        raise ValidationError("EXPORT-SCOPE-TRACKED-WORKTREE-DIRTY")


def _tracked_rows(repo_root: Path, scope: str) -> list[tuple[str, str, str]]:
    raw = _git(repo_root, "ls-files", "-s", "-z", "--", scope, text=False)
    assert isinstance(raw, bytes)
    rows: list[tuple[str, str, str]] = []
    for item in (part for part in raw.split(b"\0") if part):
        try:
            metadata, raw_path = item.split(b"\t", 1)
            mode, blob_sha1, stage = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValidationError("EXPORT-SCOPE-TRACKED-PATH-INVALID") from exc
        if stage != "0" or mode not in {"100644", "100755"}:
            raise ValidationError("EXPORT-SCOPE-NONREGULAR-TRACKED-ENTRY")
        rows.append((path, mode, blob_sha1))
    if not rows:
        raise ValidationError("EXPORT-SCOPE-NO-TRACKED-FILES")
    return sorted(rows)


def _proposal(path: str) -> tuple[str, str, str]:
    """Return a conservative state, reason, and public-surface implication.

    Classification is intentionally mechanical, not a source/right conclusion.
    Candidate-only evidence and local measurements are held pending their own
    claim/evidence review.  Everything else is merely proposed for later
    origin/rights review, never cleared by this registry.
    """

    name = PurePosixPath(path).name
    if path == "candidate-dependency-source-inventory-v0.json":
        return (
            "hold",
            "Private pre-SBOM candidate inventory; retain for review but do not treat as a final public supply-chain artifact.",
            "Could explain candidate dependency scope after independent SPDX/SBOM and rights review; cannot be presented as either today.",
        )
    if path.startswith("docs/") and ("CANDIDATE" in name or "LOCAL_MEASUREMENT" in name):
        return (
            "hold",
            "Candidate narrative or local measurement material requires separate claim, bilingual, and evidence review.",
            "May become supporting documentation only after its statements are independently bounded; it is not current public proof.",
        )
    if path.startswith("tests/"):
        return (
            "include_proposed",
            "Tracked test material is proposed so an eventual public candidate can expose its reproducibility surface; origin and rights slots remain unfilled.",
            "Would make the candidate's test contract inspectable, without proving clean-room success or runtime behavior.",
        )
    if path.startswith("src/") or path.startswith("tools/") or path in {"pyproject.toml", "README.md"}:
        return (
            "include_proposed",
            "Tracked first-party candidate source, tooling, metadata, or entry material is proposed for later export review; this is not a rights finding.",
            "Would define the inspectable candidate implementation or entry surface if later independently cleared for export.",
        )
    return (
        "hold",
        "Tracked material has no automatic public disposition and is held pending scoped origin, rights, and public-surface review.",
        "No public implication is approved; a later reviewer must decide whether it belongs in an exported candidate.",
    )


def _slot(record_id: str, kind: str) -> dict[str, str]:
    return {"slot_id": f"{kind}-evidence:{record_id}", "state": "unfilled"}


def build_candidate_export_scope_registry(*, repo: Path, scope_root: Path) -> dict[str, Any]:
    """Build an in-memory registry for an unchanged, tracked Git subtree."""

    repo_root, scope, resolved_scope = _repo_and_scope(repo, scope_root)
    _assert_clean_scope(repo_root, scope)
    scope_tree_sha1 = str(_git(repo_root, "rev-parse", f"HEAD:{scope}")).strip()
    observed_head = str(_git(repo_root, "rev-parse", "HEAD")).strip()
    records: list[dict[str, Any]] = []
    for tracked_path, mode, blob_sha1 in _tracked_rows(repo_root, scope):
        absolute = repo_root / tracked_path
        if absolute.is_symlink() or not absolute.is_file():
            raise ValidationError("EXPORT-SCOPE-WORKTREE-ENTRY-UNSAFE")
        relative = _relative(resolved_scope, absolute.resolve(strict=True))
        state, reason, implication = _proposal(relative)
        if state not in _STATES:  # defensive guard for future classifiers
            raise ValidationError("EXPORT-SCOPE-PROPOSAL-STATE-INVALID")
        record_id = "ledger-file-" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
        records.append(
            {
                "record_id": record_id,
                "path": relative,
                "git_mode": mode,
                "git_blob_sha1": "sha1:" + blob_sha1,
                "sha256": _sha256(absolute),
                "bytes": absolute.stat().st_size,
                "proposal_state": state,
                "reason": reason,
                "origin_evidence_slot": _slot(record_id, "origin"),
                "rights_evidence_slot": _slot(record_id, "rights"),
                "public_surface_implication": implication,
            }
        )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "registry_id": "ledger-candidate-export-scope-" + scope_tree_sha1[:16],
        "scope": {
            "repository_head_at_observation": observed_head,
            "scope_path": scope,
            "scope_tree_sha1": "sha1:" + scope_tree_sha1,
        },
        "records": records,
        "boundary": _BOUNDARY,
    }
    registry = {**unsigned, "registry_hash": canonical_hash(unsigned)}
    validate_payload(registry, SCHEMA, "Candidate export scope registry")
    return registry


def _comparison_projection(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("registry_hash", None)
    scope = dict(result["scope"])
    scope.pop("repository_head_at_observation", None)
    result["scope"] = scope
    return result


def write_candidate_export_scope_registry(*, repo: Path, scope_root: Path, output: Path) -> dict[str, Any]:
    """Write a new immutable private-staging proposal registry."""

    registry = build_candidate_export_scope_registry(repo=repo, scope_root=scope_root)
    atomic_create_json(output, registry)
    return registry


def verify_candidate_export_scope_registry(*, repo: Path, scope_root: Path, registry_path: Path) -> dict[str, Any]:
    """Fail closed when a tracked Ledger byte or proposal has drifted."""

    registry = load_validated_json(registry_path, SCHEMA, "Candidate export scope registry")
    verify_self_hash(registry, "registry_hash")
    current = build_candidate_export_scope_registry(repo=repo, scope_root=scope_root)
    if _comparison_projection(registry) != _comparison_projection(current):
        raise ValidationError("EXPORT-SCOPE-REGISTRY-STALE-OR-MISMATCHED")
    return registry
