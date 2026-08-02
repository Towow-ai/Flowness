from __future__ import annotations

import hashlib
import json
import re
import tarfile
import tempfile
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .controller import _hash_file, _load_roles, _load_run_manifest
from .integrity import canonical_hash
from .models import DriftFinding, EvidenceRef, MechanismCard, UnknownRecord
from .registry import (
    ValidationError,
    atomic_create_json,
    validate_drift,
    validate_mechanism,
    validate_unknown,
)
from .schema_validation import load_validated_json

OUTPUT_FILES = {
    "mechanisms": "mechanisms-registry.json",
    "unknowns": "unknown-registry.json",
    "drift_findings": "drift-registry.json",
    "report": "reconciliation-report.json",
}
LOCATOR_PATTERNS = (
    re.compile(r"^(?P<path>.+?)#L(?P<start>[0-9]+)(?:-L?(?P<end>[0-9]+))?$"),
    re.compile(r"^(?P<path>.+?):L?(?P<start>[0-9]+)(?:-L?(?P<end>[0-9]+))?$"),
    re.compile(
        r"^(?P<path>.+?)\s*\(lines?\s+(?P<start>[0-9]+)"
        r"(?:-(?P<end>[0-9]+))?\)$",
        re.IGNORECASE,
    ),
)
METADATA_ALIAS_KEYS = {
    "snapshot_id",
    "source_snapshot_id",
    "manifest_id",
    "archive_path",
    "path",
    "repository",
}
SOURCE_ARCHIVE_EVIDENCE_KINDS = {"code", "test", "schema"}


def _known_snapshot_aliases(value: str) -> list[str]:
    """Return only deterministic, distribution-specific filename aliases."""
    aliases = [value]
    name = Path(value).name
    if name not in aliases:
        aliases.append(name)
    for suffix in (".tar.gz", ".metadata.json", ".json", ".tar", ".zip"):
        if name.endswith(suffix) and len(name) > len(suffix):
            stripped = name[: -len(suffix)]
            if suffix == ".metadata.json":
                stripped += ".metadata"
            if stripped not in aliases:
                aliases.append(stripped)
            break
    return aliases


def _sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"cannot load {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"{label} must be a JSON object")
    return payload


def _sealed_evidence_contract(path: Path) -> tuple[set[str], set[str]]:
    """Return explicitly trusted non-source evidence kinds and bases."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set(), set()
    if not isinstance(payload, dict):
        return set(), set()
    if (
        payload.get("schema_version") == "repository-inventory/v1"
        or "inventory_semantics" in payload
    ):
        return set(), set()
    contract = payload.get("evidence_contract")
    if not isinstance(contract, dict):
        return set(), set()
    kinds = contract.get("allowed_kinds")
    bases = contract.get("allowed_bases")
    if not isinstance(kinds, list) or not isinstance(bases, list):
        return set(), set()
    if not all(isinstance(item, str) and item for item in kinds + bases):
        return set(), set()
    return set(kinds), set(bases)


def _safe_archive_name(name: str) -> PurePosixPath:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValidationError(f"archive member escapes extraction root: {name}")
    return normalized


def _archive_repository_root(extraction_root: Path) -> Path:
    children = sorted(
        (item for item in extraction_root.iterdir() if item.name != "__MACOSX"),
        key=lambda item: item.name,
    )
    if len(children) == 1 and children[0].is_dir() and not children[0].is_symlink():
        return children[0]
    return extraction_root


@contextmanager
def _source_repository(source: Path) -> Iterator[tuple[Path, dict[str, str]]]:
    source = source.resolve()
    if source.is_dir():
        yield source, {
            "kind": "directory",
            "path": str(source),
            "sha256": _hash_tree(source),
        }
        return
    if source.is_symlink() or not source.is_file():
        raise ValidationError(f"source must be a regular archive or directory: {source}")
    with tempfile.TemporaryDirectory(prefix="flowness-reconcile-") as temporary:
        extraction_root = Path(temporary)
        if tarfile.is_tarfile(source):
            with tarfile.open(source, "r:*") as archive:
                members = archive.getmembers()
                for member in members:
                    _safe_archive_name(member.name)
                    if member.issym() or member.islnk() or member.isdev():
                        raise ValidationError(
                            f"unsupported archive member type: {member.name}"
                        )
                archive.extractall(extraction_root, members=members, filter="data")
        elif zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    _safe_archive_name(info.filename)
                    unix_mode = (info.external_attr >> 16) & 0o170000
                    if unix_mode == 0o120000:
                        raise ValidationError(
                            f"unsupported archive symlink: {info.filename}"
                        )
                archive.extractall(extraction_root)
        else:
            raise ValidationError(f"unsupported source archive: {source}")
        repository_root = _archive_repository_root(extraction_root)
        yield repository_root, {
            "kind": "archive",
            "path": str(source),
            "sha256": _sha256_bytes(source.read_bytes()),
        }


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (
            item
            for item in root.rglob("*")
            if item.is_file() and not item.is_symlink()
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


class SourceResolver:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files: dict[str, Path] = {}
        for item in self.root.rglob("*"):
            if item.is_symlink():
                continue
            if item.is_file():
                self.files[item.relative_to(self.root).as_posix()] = item

    @staticmethod
    def split_locator(locator: str) -> tuple[str, int | None, int | None]:
        value = locator.strip().replace("\\", "/")
        for pattern in LOCATOR_PATTERNS:
            match = pattern.fullmatch(value)
            if match:
                start = int(match.group("start"))
                end = int(match.group("end") or start)
                return match.group("path").strip(), start, end
        return value, None, None

    @staticmethod
    def split_locator_ranges(locator: str) -> tuple[str, list[tuple[int, int]]]:
        value = locator.strip().replace("\\", "/")
        match = re.fullmatch(
            r"(?P<path>.+):(?P<ranges>[0-9]+(?:-[0-9]+)?"
            r"(?:\s*,\s*[0-9]+(?:-[0-9]+)?)*)",
            value,
        )
        if match:
            ranges: list[tuple[int, int]] = []
            for raw_range in match.group("ranges").split(","):
                pieces = raw_range.strip().split("-", 1)
                start = int(pieces[0])
                end = int(pieces[1]) if len(pieces) == 2 else start
                ranges.append((start, end))
            return match.group("path").strip(), ranges
        raw_path, start, end = SourceResolver.split_locator(value)
        if start is None or end is None:
            return raw_path, []
        return raw_path, [(start, end)]

    def resolve(self, locator: str) -> tuple[Path, str, int | None, int | None]:
        raw_path, start, end = self.split_locator(locator)
        raw_path = raw_path.removeprefix("file://").strip()
        while raw_path.startswith("./"):
            raw_path = raw_path[2:]
        pure = PurePosixPath(raw_path)
        if ".." in pure.parts:
            raise ValidationError(f"locator escapes repository: {locator}")

        direct = raw_path.lstrip("/")
        if direct in self.files:
            relative = direct
        else:
            matches: list[tuple[int, str]] = []
            raw_parts = tuple(part for part in pure.parts if part not in {"", "/"})
            for relative_path in self.files:
                relative_parts = PurePosixPath(relative_path).parts
                max_suffix = min(len(raw_parts), len(relative_parts))
                matched = 0
                for width in range(max_suffix, 0, -1):
                    if raw_parts[-width:] == relative_parts[-width:]:
                        matched = width
                        break
                if matched:
                    matches.append((matched, relative_path))
            if not matches:
                raise ValidationError(f"locator does not name a source file: {locator}")
            best_width = max(width for width, _ in matches)
            best = sorted(path for width, path in matches if width == best_width)
            if len(best) != 1:
                raise ValidationError(
                    f"locator is ambiguous at repository boundary: {locator}"
                )
            relative = best[0]
        if start is not None and (start < 1 or end is None or end < start):
            raise ValidationError(f"invalid locator line range: {locator}")
        canonical = relative
        if start is not None:
            canonical += f":{start}-{end}"
        return self.files[relative], canonical, start, end

    def resolve_ranges(
        self, locator: str
    ) -> tuple[Path, str, list[tuple[int, int]]]:
        raw_path, ranges = self.split_locator_ranges(locator)
        path, canonical_path, _, _ = self.resolve(raw_path)
        for start, end in ranges:
            if start < 1 or end < start:
                raise ValidationError(f"invalid locator line range: {locator}")
        canonical = canonical_path
        if ranges:
            canonical += ":" + ",".join(
                f"{start}-{end}" for start, end in ranges
            )
        return path, canonical, ranges

    def actual_excerpt(self, path: Path, start: int, end: int) -> str:
        content = path.read_bytes()
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(f"excerpt source is not UTF-8: {path}") from exc
        lines = content.splitlines(keepends=True)
        if start > len(lines) or end > len(lines):
            raise ValidationError(
                f"excerpt range {start}-{end} exceeds {len(lines)} lines: {path}"
            )
        excerpt_bytes = b"".join(lines[start - 1 : end])
        return excerpt_bytes.decode("utf-8")


def _snapshot_aliases(
    run_payload: dict[str, Any],
) -> tuple[dict[str, str], set[str], list[dict[str, str]]]:
    snapshots = run_payload.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise ValidationError("run manifest requires snapshots")
    direct_candidates: dict[str, set[str]] = {}
    metadata_candidates: dict[str, set[str]] = {}
    records: list[dict[str, str]] = []

    def add(
        candidates: dict[str, set[str]], alias: str, canonical: str
    ) -> None:
        if alias:
            candidates.setdefault(alias, set()).add(canonical)

    def add_metadata_aliases(value: Any, canonical: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in METADATA_ALIAS_KEYS and isinstance(child, str) and child:
                    add(metadata_candidates, child, canonical)
                    add(metadata_candidates, Path(child).name, canonical)
                elif isinstance(child, (dict, list)):
                    add_metadata_aliases(child, canonical)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    add_metadata_aliases(child, canonical)

    metadata_payloads: list[dict[str, Any]] = []
    for index, record in enumerate(snapshots):
        if not isinstance(record, dict):
            raise ValidationError(f"run snapshot {index} must be an object")
        raw_path = record.get("path")
        raw_sha = record.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(raw_sha, str):
            raise ValidationError(f"run snapshot {index} lacks path/sha256")
        path = Path(raw_path).resolve()
        if _hash_file(path) != raw_sha:
            raise ValidationError(f"sealed run snapshot changed: {path}")
        canonical = "sha256:" + raw_sha
        add(direct_candidates, canonical, canonical)
        add(direct_candidates, raw_sha, canonical)
        for alias in _known_snapshot_aliases(raw_path):
            add(direct_candidates, alias, canonical)
        for alias in _known_snapshot_aliases(str(path)):
            add(direct_candidates, alias, canonical)
        records.append(
            {"path": str(path), "sha256": canonical, "basename": path.name}
        )
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            metadata = None
        add_metadata_aliases(metadata, canonical)
        if isinstance(metadata, dict):
            metadata_payloads.append(metadata)

    # A source-export prefix is accepted only when the same sealed metadata
    # binds both its archive path and archive digest to one sealed input.
    for metadata in metadata_payloads:
        prefix = metadata.get("archive_prefix")
        archive_path = metadata.get("archive_path")
        archive_sha = metadata.get("archive_sha256")
        if not all(isinstance(item, str) and item for item in (
            prefix,
            archive_path,
            archive_sha,
        )):
            continue
        sha_aliases = [archive_sha]
        if not archive_sha.startswith("sha256:"):
            sha_aliases.append("sha256:" + archive_sha)
        path_values: set[str] = set()
        for alias in _known_snapshot_aliases(archive_path):
            path_values.update(direct_candidates.get(alias, set()))
        sha_values: set[str] = set()
        for alias in sha_aliases:
            sha_values.update(direct_candidates.get(alias, set()))
        if len(path_values) == 1 and path_values == sha_values:
            add(
                metadata_candidates,
                prefix.rstrip("/"),
                next(iter(path_values)),
            )
    # A sealed file's own path, basename, or digest is authoritative. Metadata
    # inside another sealed JSON input must never steal that direct alias.
    candidates: dict[str, set[str]] = {}
    for alias, values in metadata_candidates.items():
        direct_values: set[str] = set()
        for related_alias in _known_snapshot_aliases(alias):
            direct_values.update(direct_candidates.get(related_alias, set()))
        candidates[alias] = direct_values or values
    candidates.update(direct_candidates)
    aliases = {
        alias: next(iter(values))
        for alias, values in candidates.items()
        if len(values) == 1
    }
    ambiguous = {alias for alias, values in candidates.items() if len(values) > 1}
    return aliases, ambiguous, records


def _add_blocker(
    blockers: list[dict[str, str]],
    code: str,
    detail: str,
    role_id: str = "",
    object_id: str = "",
) -> None:
    blockers.append(
        {
            "code": code,
            "role_id": role_id,
            "object_id": object_id,
            "detail": detail,
        }
    )


def _canonical_snapshot_id(
    value: str,
    aliases: dict[str, str],
    ambiguous: set[str],
    blockers: list[dict[str, str]],
    role_id: str,
    object_id: str,
) -> str | None:
    lookup_values = _known_snapshot_aliases(value)
    ambiguous_value = next((item for item in lookup_values if item in ambiguous), None)
    if ambiguous_value is not None:
        _add_blocker(
            blockers,
            "ambiguous_snapshot_alias",
            f"snapshot alias maps to multiple sealed inputs: {ambiguous_value}",
            role_id,
            object_id,
        )
        return None
    canonical = next(
        (aliases[item] for item in lookup_values if item in aliases),
        None,
    )
    if canonical is None:
        _add_blocker(
            blockers,
            "unknown_snapshot_alias",
            f"snapshot alias is not sealed by run.json: {value}",
            role_id,
            object_id,
        )
    return canonical


def _evidence_ref(payload: dict[str, Any]) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=payload["evidence_id"],
        kind=payload["kind"],
        locator=payload["locator"],
        source_snapshot_id=payload["source_snapshot_id"],
        content_hash=payload["content_hash"],
        captured_at=payload["captured_at"],
        independent_group=payload["independent_group"],
        summary=payload["summary"],
        basis=payload["basis"],
        observed_excerpt=payload["observed_excerpt"],
        hash_scope=payload["hash_scope"],
    )


def _normalize_evidence(
    evidence: dict[str, Any],
    role_id: str,
    resolver: SourceResolver,
    aliases: dict[str, str],
    ambiguous_aliases: set[str],
    blockers: list[dict[str, str]],
    id_mappings: list[dict[str, str]],
    locator_mappings: list[dict[str, str]],
    snapshot_mappings: list[dict[str, str]],
    source_archive_snapshot_id: str | None,
    sealed_snapshot_paths: dict[str, Path],
    evidence_transformations: list[dict[str, Any]],
    declared_snapshot_ids: set[str],
) -> list[dict[str, Any]]:
    original_id = evidence["evidence_id"]
    canonical_snapshot = _canonical_snapshot_id(
        evidence["source_snapshot_id"],
        aliases,
        ambiguous_aliases,
        blockers,
        role_id,
        original_id,
    )
    if canonical_snapshot is None:
        return []
    if canonical_snapshot not in declared_snapshot_ids:
        _add_blocker(
            blockers,
            "evidence_snapshot_not_declared_by_role",
            "evidence references a sealed input absent from result.snapshot_ids",
            role_id,
            original_id,
        )
        return []
    expected_hash = evidence["content_hash"]
    if evidence["kind"] not in SOURCE_ARCHIVE_EVIDENCE_KINDS:
        snapshot_path = sealed_snapshot_paths.get(canonical_snapshot)
        if snapshot_path is None:
            _add_blocker(
                blockers,
                "non_source_evidence_snapshot_missing",
                "non-source evidence does not resolve to a sealed input file",
                role_id,
                original_id,
            )
            return []
        allowed_kinds, allowed_bases = _sealed_evidence_contract(snapshot_path)
        if (
            evidence["kind"] not in allowed_kinds
            or evidence["basis"] not in allowed_bases
        ):
            _add_blocker(
                blockers,
                "non_source_evidence_type_untrusted",
                (
                    "sealed input does not explicitly authorize this evidence "
                    "kind and basis"
                ),
                role_id,
                original_id,
            )
            return []
        raw_locator, ranges = resolver.split_locator_ranges(evidence["locator"])
        if len(ranges) != 1:
            _add_blocker(
                blockers,
                "non_source_evidence_locator_unbound",
                "non-source evidence requires one sealed-input line range",
                role_id,
                original_id,
            )
            return []
        locator_snapshot = _canonical_snapshot_id(
            raw_locator,
            aliases,
            ambiguous_aliases,
            blockers,
            role_id,
            original_id,
        )
        if locator_snapshot != canonical_snapshot:
            _add_blocker(
                blockers,
                "non_source_evidence_locator_unbound",
                "non-source locator does not resolve to its declared sealed input",
                role_id,
                original_id,
            )
            return []
        start, end = ranges[0]
        snapshot_bytes = snapshot_path.read_bytes()
        if evidence["hash_scope"] == "exact_file_bytes":
            actual_hash = _sha256_bytes(snapshot_bytes)
        try:
            snapshot_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _add_blocker(
                blockers,
                "non_source_evidence_snapshot_not_utf8",
                "non-source observed excerpt cannot be found in the sealed input",
                role_id,
                original_id,
            )
            return []
        try:
            lines = snapshot_bytes.splitlines(keepends=True)
            if start < 1 or end < start or end > len(lines):
                raise ValidationError("sealed-input line range is invalid")
            observed_excerpt = b"".join(lines[start - 1 : end]).decode("utf-8")
        except (UnicodeDecodeError, ValidationError) as exc:
            _add_blocker(
                blockers,
                "non_source_evidence_locator_unbound",
                str(exc),
                role_id,
                original_id,
            )
            return []
        if evidence["hash_scope"] != "exact_file_bytes":
            actual_hash = _sha256_bytes(observed_excerpt.encode("utf-8"))
        if actual_hash != expected_hash:
            _add_blocker(
                blockers,
                "evidence_content_hash_mismatch",
                f"declared {expected_hash}, actual {actual_hash}",
                role_id,
                original_id,
            )
            return []
        namespaced_id = f"{role_id}::{original_id}"
        snapshot_mappings.append(
            {
                "role_id": role_id,
                "original": evidence["source_snapshot_id"],
                "canonical": canonical_snapshot,
            }
        )
        id_mappings.append(
            {
                "kind": "evidence",
                "role_id": role_id,
                "original_id": original_id,
                "canonical_id": namespaced_id,
            }
        )
        return [
            {
                **evidence,
                "evidence_id": namespaced_id,
                "original_id": original_id,
                "source_role_id": role_id,
                "locator": f"{snapshot_path.name}:{start}-{end}",
                "source_snapshot_id": canonical_snapshot,
                "observed_excerpt": observed_excerpt,
                "content_hash": actual_hash,
            }
        ]

    if source_archive_snapshot_id is None:
        _add_blocker(
            blockers,
            "evidence_has_no_sealed_source_archive",
            "code/test/schema evidence has no run-sealed source archive",
            role_id,
            original_id,
        )
        return []
    if canonical_snapshot != source_archive_snapshot_id:
        _add_blocker(
            blockers,
            "evidence_snapshot_not_source_archive",
            (
                "code/test/schema evidence source_snapshot_id does not resolve "
                "to the run-sealed source archive"
            ),
            role_id,
            original_id,
        )
        return []
    try:
        path, canonical_locator, ranges = resolver.resolve_ranges(
            evidence["locator"]
        )
    except ValidationError as exc:
        _add_blocker(
            blockers,
            "invalid_evidence_locator",
            str(exc),
            role_id,
            original_id,
        )
        return []

    snapshot_mappings.append(
        {
            "role_id": role_id,
            "original": evidence["source_snapshot_id"],
            "canonical": canonical_snapshot,
        }
    )
    if evidence["hash_scope"] == "exact_file_bytes":
        actual_bytes = path.read_bytes()
        actual_file_hash = _sha256_bytes(actual_bytes)
        if actual_file_hash != expected_hash:
            _add_blocker(
                blockers,
                "evidence_content_hash_mismatch",
                (
                    f"declared {expected_hash}, actual {actual_file_hash} "
                    f"at {canonical_locator}"
                ),
                role_id,
                original_id,
            )
            return []
        if ranges:
            canonical_base = canonical_locator.rsplit(":", 1)[0]
            normalized: list[dict[str, Any]] = []
            derived_records: list[dict[str, str]] = []
            for index, (start, end) in enumerate(ranges, 1):
                try:
                    excerpt = resolver.actual_excerpt(path, start, end)
                except ValidationError as exc:
                    _add_blocker(
                        blockers,
                        "invalid_excerpt_range",
                        str(exc),
                        role_id,
                        original_id,
                    )
                    return []
                derived_id = f"{role_id}::{original_id}::r{index}"
                derived_locator = f"{canonical_base}:{start}-{end}"
                excerpt_hash = _sha256_bytes(excerpt.encode("utf-8"))
                id_mappings.append(
                    {
                        "kind": "evidence",
                        "role_id": role_id,
                        "original_id": original_id,
                        "canonical_id": derived_id,
                    }
                )
                locator_mappings.append(
                    {
                        "role_id": role_id,
                        "original": evidence["locator"],
                        "canonical": derived_locator,
                    }
                )
                derived_records.append(
                    {
                        "evidence_id": derived_id,
                        "locator": derived_locator,
                        "content_hash": excerpt_hash,
                    }
                )
                normalized.append(
                    {
                        **evidence,
                        "evidence_id": derived_id,
                        "original_id": original_id,
                        "source_role_id": role_id,
                        "locator": derived_locator,
                        "source_snapshot_id": canonical_snapshot,
                        "observed_excerpt": excerpt,
                        "hash_scope": "exact_excerpt_utf8",
                        "content_hash": excerpt_hash,
                    }
                )
            evidence_transformations.append(
                {
                    "role_id": role_id,
                    "original_id": original_id,
                    "transformation": "exact_file_ranges_to_exact_excerpts",
                    "original_locator": evidence["locator"],
                    "parent_content_hash": actual_file_hash,
                    "derived": derived_records,
                }
            )
            return normalized
        try:
            actual_text = actual_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _add_blocker(
                blockers,
                "exact_file_observation_not_utf8",
                (
                    "exact_file_bytes cannot verify a UTF-8 observed_excerpt: "
                    f"{canonical_locator}"
                ),
                role_id,
                original_id,
            )
            return []
        if evidence["observed_excerpt"] not in actual_text:
            _add_blocker(
                blockers,
                "evidence_observed_excerpt_mismatch",
                f"observed_excerpt is not an exact substring of {canonical_locator}",
                role_id,
                original_id,
            )
            return []
        actual_hash = actual_file_hash
        observed_excerpt = evidence["observed_excerpt"]
    else:
        if len(ranges) != 1:
            _add_blocker(
                blockers,
                "excerpt_locator_requires_one_line_range",
                f"exact_excerpt_utf8 requires one line range: {evidence['locator']}",
                role_id,
                original_id,
            )
            return []
        start, end = ranges[0]
        try:
            observed_excerpt = resolver.actual_excerpt(path, start, end)
        except ValidationError as exc:
            _add_blocker(
                blockers,
                "invalid_excerpt_range",
                str(exc),
                role_id,
                original_id,
            )
            return []
        actual_hash = _sha256_bytes(observed_excerpt.encode("utf-8"))
        if actual_hash != expected_hash:
            _add_blocker(
                blockers,
                "evidence_content_hash_mismatch",
                f"declared {expected_hash}, actual {actual_hash} at {canonical_locator}",
                role_id,
                original_id,
            )
            return []

    namespaced_id = f"{role_id}::{original_id}"
    id_mappings.append(
        {
            "kind": "evidence",
            "role_id": role_id,
            "original_id": original_id,
            "canonical_id": namespaced_id,
        }
    )
    if evidence["locator"] != canonical_locator:
        locator_mappings.append(
            {
                "role_id": role_id,
                "original": evidence["locator"],
                "canonical": canonical_locator,
            }
        )
    normalized = {
        **evidence,
        "evidence_id": namespaced_id,
        "original_id": original_id,
        "source_role_id": role_id,
        "locator": canonical_locator,
        "source_snapshot_id": canonical_snapshot,
        "observed_excerpt": observed_excerpt,
        "content_hash": actual_hash,
    }
    return [normalized]


def _normalize_soft_locator(
    locator: str,
    role_id: str,
    resolver: SourceResolver,
    locator_mappings: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> str:
    try:
        _, canonical, _ = resolver.resolve_ranges(locator)
    except ValidationError as exc:
        if Path(SourceResolver.split_locator(locator)[0]).is_absolute():
            warnings.append(
                {
                    "code": "noncanonical_non_evidence_locator",
                    "role_id": role_id,
                    "detail": str(exc),
                }
            )
        return locator
    if canonical != locator:
        locator_mappings.append(
            {"role_id": role_id, "original": locator, "canonical": canonical}
        )
    return canonical


def _mechanism_card(payload: dict[str, Any]) -> MechanismCard:
    return MechanismCard(
        mechanism_id=payload["mechanism_id"],
        public_name=payload["public_name"],
        internal_names=payload["internal_names"],
        problem_and_failure=payload["problem_and_failure"],
        status=payload["status"],
        triggers=payload["triggers"],
        inputs=payload["inputs"],
        outputs=payload["outputs"],
        authoritative_state=payload["authoritative_state"],
        state_transitions=payload["state_transitions"],
        invariants=payload["invariants"],
        authority_and_permissions=payload["authority_and_permissions"],
        producers=payload["producers"],
        consumers=payload["consumers"],
        dependencies=payload["dependencies"],
        failure_modes=payload["failure_modes"],
        recovery_and_rollback=payload["recovery_and_rollback"],
        inventory_item_ids=payload["inventory_item_ids"],
        evidence=[_evidence_ref(item) for item in payload["evidence"]],
        known_drift=payload["known_drift"],
        public_scope=payload["public_scope"],
        public_claims=payload["public_claims"],
        confidence=payload["confidence"],
        unresolved_questions=payload["unresolved_questions"],
        verification_trace=payload["verification_trace"],
    )


def _unknown_record(payload: dict[str, Any]) -> UnknownRecord:
    return UnknownRecord(
        unknown_id=payload["unknown_id"],
        inventory_item_id=payload["inventory_item_id"],
        object_type=payload["object_type"],
        locator=payload["locator"],
        question=payload["question"],
        blocking=payload["blocking"],
        evidence=[_evidence_ref(item) for item in payload["evidence"]],
        next_check=payload["next_check"],
    )


def _drift_finding(payload: dict[str, Any]) -> DriftFinding:
    return DriftFinding(
        drift_id=payload["drift_id"],
        surface_from=payload["surface_from"],
        surface_to=payload["surface_to"],
        source_locators=payload["source_locators"],
        affected_consumers=payload["affected_consumers"],
        severity=payload["severity"],
        public_impact=payload["public_impact"],
        remediation_or_downgrade=payload["remediation_or_downgrade"],
        state=payload["state"],
        evidence=[_evidence_ref(item) for item in payload["evidence"]],
    )


def reconcile_excavation(
    run_path: Path,
    result_paths: list[Path],
    source: Path,
    schema_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if not result_paths:
        raise ValidationError("at least one producer result is required")
    output_dir = output_dir.resolve()
    targets = {key: output_dir / name for key, name in OUTPUT_FILES.items()}
    existing = [str(path) for path in targets.values() if path.exists()]
    if existing:
        raise ValidationError(
            "refusing to overwrite reconciliation output: " + ", ".join(existing)
        )

    run_path = run_path.resolve()
    schema_path = schema_path.resolve()
    run_payload = _load_run_manifest(run_path)
    run_id = run_payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValidationError("run manifest requires run_id")

    roles_record = run_payload.get("roles_config")
    if not isinstance(roles_record, dict):
        raise ValidationError("run manifest requires roles_config")
    roles_path = Path(str(roles_record.get("path", ""))).resolve()
    if _hash_file(roles_path) != roles_record.get("sha256"):
        raise ValidationError("roles config differs from sealed run manifest")
    roles = {role.role_id: role for role in _load_roles(roles_path)}
    aliases, ambiguous_aliases, snapshot_records = _snapshot_aliases(run_payload)
    expected_snapshot_ids = {record["sha256"] for record in snapshot_records}
    sealed_snapshot_paths = {
        record["sha256"]: Path(record["path"]) for record in snapshot_records
    }

    result_records: list[tuple[str, Path, dict[str, Any]]] = []
    seen_roles: set[str] = set()
    for result_path in result_paths:
        resolved = result_path.resolve()
        payload = load_validated_json(
            resolved,
            schema_path,
            f"producer result {resolved}",
        )
        role_id = payload["role_id"]
        role = roles.get(role_id)
        if (
            role is None
            or role.kind != "producer"
            or role.output_schema != schema_path.name
        ):
            raise ValidationError(
                f"result role/schema is not sealed by run manifest: {role_id}"
            )
        if role_id in seen_roles:
            raise ValidationError(f"duplicate producer result for role: {role_id}")
        seen_roles.add(role_id)
        if resolved.parent.parent.name == "producers" and resolved.parent.name != role_id:
            raise ValidationError(
                f"result path role differs from payload role: {resolved}"
            )
        result_records.append((role_id, resolved, payload))
    result_records.sort(key=lambda item: item[0])

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    id_mappings: list[dict[str, str]] = []
    locator_mappings: list[dict[str, str]] = []
    snapshot_mappings: list[dict[str, str]] = []
    source_path_mappings: list[dict[str, str]] = []
    evidence_transformations: list[dict[str, Any]] = []
    evidence_definitions: dict[tuple[str, str], dict[str, Any]] = {}
    mechanisms: dict[str, dict[str, Any]] = {}
    mechanism_definitions: dict[str, dict[str, Any]] = {}
    unknowns: list[dict[str, Any]] = []
    drift_findings: list[dict[str, Any]] = []

    with _source_repository(source) as (repository_root, source_record):
        resolver = SourceResolver(repository_root)
        source_archive_snapshot_id: str | None = None
        if (
            source_record["kind"] == "archive"
            and source_record["sha256"] in expected_snapshot_ids
        ):
            source_archive_snapshot_id = source_record["sha256"]
        else:
            _add_blocker(
                blockers,
                "source_archive_not_sealed_by_run",
                "--source must be an archive whose content hash is sealed by run.json",
            )
        for role_id, _, result in result_records:
            normalized_snapshot_ids: set[str] = set()
            for original_snapshot_id in result["snapshot_ids"]:
                canonical_snapshot_id = _canonical_snapshot_id(
                    original_snapshot_id,
                    aliases,
                    ambiguous_aliases,
                    blockers,
                    role_id,
                    "result.snapshot_ids",
                )
                if canonical_snapshot_id is not None:
                    normalized_snapshot_ids.add(canonical_snapshot_id)
                    snapshot_mappings.append(
                        {
                            "role_id": role_id,
                            "original": original_snapshot_id,
                            "canonical": canonical_snapshot_id,
                        }
                    )
            if not normalized_snapshot_ids or not normalized_snapshot_ids.issubset(
                expected_snapshot_ids
            ):
                _add_blocker(
                    blockers,
                    "result_snapshot_set_mismatch",
                    "result snapshot_ids must resolve to a non-empty sealed subset",
                    role_id,
                    "result.snapshot_ids",
                )
            if (
                source_archive_snapshot_id is not None
                and source_archive_snapshot_id not in normalized_snapshot_ids
            ):
                _add_blocker(
                    blockers,
                    "result_source_snapshot_missing",
                    "result snapshot_ids must reference the sealed source archive",
                    role_id,
                    "result.snapshot_ids",
                )

            for archive in result["source_inspection"]["archives"]:
                archive_snapshot = _canonical_snapshot_id(
                    archive["archive_path"],
                    aliases,
                    ambiguous_aliases,
                    blockers,
                    role_id,
                    "source_inspection.archive_path",
                )
                if (
                    archive_snapshot is not None
                    and archive_snapshot != archive["archive_content_hash"]
                ):
                    _add_blocker(
                        blockers,
                        "archive_content_hash_mismatch",
                        "source_inspection archive hash differs from run-sealed file",
                        role_id,
                        archive["archive_path"],
                    )
                if (
                    source_archive_snapshot_id is not None
                    and (
                        archive_snapshot != source_archive_snapshot_id
                        or archive["archive_content_hash"]
                        != source_archive_snapshot_id
                    )
                ):
                    _add_blocker(
                        blockers,
                        "source_inspection_archive_not_source",
                        (
                            "source_inspection archive does not identify the "
                            "run-sealed --source archive"
                        ),
                        role_id,
                        archive["archive_path"],
                    )
                if archive_snapshot is not None:
                    source_path_mappings.append(
                        {
                            "kind": "archive",
                            "role_id": role_id,
                            "original": archive["archive_path"],
                            "canonical": archive_snapshot,
                        }
                    )
                source_path_mappings.append(
                    {
                        "kind": "extraction_root",
                        "role_id": role_id,
                        "original": archive["extraction_root"],
                        "canonical": ".",
                    }
                )
                if not archive["extracted"]:
                    _add_blocker(
                        blockers,
                        "source_archive_not_extracted",
                        "producer did not extract the sealed source archive",
                        role_id,
                        archive["archive_path"],
                    )
                for locator in (
                    archive["source_files_read"] + archive["test_files_read"]
                ):
                    try:
                        _, canonical, _, _ = resolver.resolve(locator)
                    except ValidationError as exc:
                        _add_blocker(
                            blockers,
                            "claimed_source_read_not_found",
                            str(exc),
                            role_id,
                            locator,
                        )
                    else:
                        if canonical != locator:
                            locator_mappings.append(
                                {
                                    "role_id": role_id,
                                    "original": locator,
                                    "canonical": canonical,
                                }
                            )

            role_drift_ids = {
                finding["drift_id"] for finding in result["drift_findings"]
            }

            def clean_evidence_list(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
                cleaned: list[dict[str, Any]] = []
                for item in items:
                    normalized_items = _normalize_evidence(
                        item,
                        role_id,
                        resolver,
                        aliases,
                        ambiguous_aliases,
                        blockers,
                        id_mappings,
                        locator_mappings,
                        snapshot_mappings,
                        source_archive_snapshot_id,
                        sealed_snapshot_paths,
                        evidence_transformations,
                        normalized_snapshot_ids,
                    )
                    for normalized in normalized_items:
                        definition_key = (role_id, normalized["evidence_id"])
                        previous = evidence_definitions.get(definition_key)
                        if previous is not None and previous != normalized:
                            _add_blocker(
                                blockers,
                                "conflicting_evidence_id_definition",
                                "same role reused an evidence_id for different evidence",
                                role_id,
                                item["evidence_id"],
                            )
                            continue
                        evidence_definitions[definition_key] = normalized
                        cleaned.append(normalized)
                return cleaned

            for mechanism in result["mechanisms"]:
                normalized = deepcopy(mechanism)
                normalized["evidence"] = clean_evidence_list(mechanism["evidence"])
                normalized["known_drift"] = [
                    f"{role_id}::{item}" if item in role_drift_ids else item
                    for item in mechanism["known_drift"]
                ]
                validation_payload = deepcopy(normalized)
                validation_payload["evidence"] = [
                    {key: value for key, value in item.items() if key not in {"original_id", "source_role_id"}}
                    for item in normalized["evidence"]
                ]
                try:
                    validate_mechanism(_mechanism_card(validation_payload))
                except ValidationError as exc:
                    _add_blocker(
                        blockers,
                        "invalid_mechanism_registry_record",
                        str(exc),
                        role_id,
                        mechanism["mechanism_id"],
                    )
                semantic_definition = {
                    key: value
                    for key, value in mechanism.items()
                    if key not in {"evidence", "known_drift"}
                }
                mechanism_id = mechanism["mechanism_id"]
                previous_definition = mechanism_definitions.get(mechanism_id)
                if previous_definition is not None and previous_definition != semantic_definition:
                    _add_blocker(
                        blockers,
                        "conflicting_mechanism_definition",
                        "same mechanism_id has different semantic definitions; no merge performed",
                        role_id,
                        mechanism_id,
                    )
                    continue
                if previous_definition is None:
                    mechanism_definitions[mechanism_id] = semantic_definition
                    mechanisms[mechanism_id] = {
                        **normalized,
                        "source_roles": [role_id],
                    }
                else:
                    existing = mechanisms[mechanism_id]
                    existing["source_roles"].append(role_id)
                    existing["evidence"].extend(normalized["evidence"])
                    existing["known_drift"].extend(normalized["known_drift"])

            for unknown in result["unknowns"]:
                original_id = unknown["unknown_id"]
                namespaced_id = f"{role_id}::{original_id}"
                normalized = {
                    **deepcopy(unknown),
                    "unknown_id": namespaced_id,
                    "original_id": original_id,
                    "source_role_id": role_id,
                    "locator": _normalize_soft_locator(
                        unknown["locator"],
                        role_id,
                        resolver,
                        locator_mappings,
                        warnings,
                    ),
                    "evidence": clean_evidence_list(unknown["evidence"]),
                }
                validation_payload = {
                    key: value
                    for key, value in normalized.items()
                    if key not in {"original_id", "source_role_id"}
                }
                validation_payload["evidence"] = [
                    {key: value for key, value in item.items() if key not in {"original_id", "source_role_id"}}
                    for item in normalized["evidence"]
                ]
                try:
                    validate_unknown(_unknown_record(validation_payload))
                except ValidationError as exc:
                    _add_blocker(
                        blockers,
                        "invalid_unknown_registry_record",
                        str(exc),
                        role_id,
                        original_id,
                    )
                id_mappings.append(
                    {
                        "kind": "unknown",
                        "role_id": role_id,
                        "original_id": original_id,
                        "canonical_id": namespaced_id,
                    }
                )
                unknowns.append(normalized)

            for finding in result["drift_findings"]:
                original_id = finding["drift_id"]
                namespaced_id = f"{role_id}::{original_id}"
                normalized = {
                    **deepcopy(finding),
                    "drift_id": namespaced_id,
                    "original_id": original_id,
                    "source_role_id": role_id,
                    "source_locators": [
                        _normalize_soft_locator(
                            locator,
                            role_id,
                            resolver,
                            locator_mappings,
                            warnings,
                        )
                        for locator in finding["source_locators"]
                    ],
                    "evidence": clean_evidence_list(finding["evidence"]),
                }
                validation_payload = {
                    key: value
                    for key, value in normalized.items()
                    if key not in {"original_id", "source_role_id"}
                }
                validation_payload["evidence"] = [
                    {key: value for key, value in item.items() if key not in {"original_id", "source_role_id"}}
                    for item in normalized["evidence"]
                ]
                try:
                    validate_drift(_drift_finding(validation_payload))
                except ValidationError as exc:
                    _add_blocker(
                        blockers,
                        "invalid_drift_registry_record",
                        str(exc),
                        role_id,
                        original_id,
                    )
                id_mappings.append(
                    {
                        "kind": "drift",
                        "role_id": role_id,
                        "original_id": original_id,
                        "canonical_id": namespaced_id,
                    }
                )
                drift_findings.append(normalized)

    mechanism_registry = sorted(mechanisms.values(), key=lambda item: item["mechanism_id"])
    for mechanism in mechanism_registry:
        mechanism["source_roles"] = sorted(set(mechanism["source_roles"]))
        mechanism["evidence"] = sorted(
            {item["evidence_id"]: item for item in mechanism["evidence"]}.values(),
            key=lambda item: item["evidence_id"],
        )
        mechanism["known_drift"] = sorted(set(mechanism["known_drift"]))
    unknown_registry = sorted(unknowns, key=lambda item: item["unknown_id"])
    drift_registry = sorted(drift_findings, key=lambda item: item["drift_id"])
    blockers = sorted(
        {json.dumps(item, sort_keys=True): item for item in blockers}.values(),
        key=lambda item: (item["code"], item["role_id"], item["object_id"], item["detail"]),
    )
    warnings = sorted(
        {json.dumps(item, sort_keys=True): item for item in warnings}.values(),
        key=lambda item: (item["code"], item["role_id"], item["detail"]),
    )
    id_mappings = sorted(
        {json.dumps(item, sort_keys=True): item for item in id_mappings}.values(),
        key=lambda item: (item["kind"], item["role_id"], item["original_id"]),
    )
    locator_mappings = sorted(
        {json.dumps(item, sort_keys=True): item for item in locator_mappings}.values(),
        key=lambda item: (item["role_id"], item["original"], item["canonical"]),
    )
    snapshot_mappings = sorted(
        {json.dumps(item, sort_keys=True): item for item in snapshot_mappings}.values(),
        key=lambda item: (item["role_id"], item["original"], item["canonical"]),
    )
    source_path_mappings = sorted(
        {
            json.dumps(item, sort_keys=True): item
            for item in source_path_mappings
        }.values(),
        key=lambda item: (
            item["kind"],
            item["role_id"],
            item["original"],
            item["canonical"],
        ),
    )
    evidence_transformations = sorted(
        {
            json.dumps(item, sort_keys=True): item
            for item in evidence_transformations
        }.values(),
        key=lambda item: (
            item["role_id"],
            item["original_id"],
            item["original_locator"],
        ),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_bindings: dict[str, dict[str, Any]] = {}
    if not blockers:
        registry_payloads = {
            "mechanisms": mechanism_registry,
            "unknowns": unknown_registry,
            "drift_findings": drift_registry,
        }
        for registry_name, payload in registry_payloads.items():
            atomic_create_json(targets[registry_name], payload)
            output_bindings[registry_name] = {
                "filename": targets[registry_name].name,
                "count": len(payload),
                "content_hash": canonical_hash(payload),
                "file_sha256": "sha256:" + _hash_file(targets[registry_name]),
            }

    unsigned_report = {
        "schema_version": "reconciliation-report/v1",
        "run_id": run_id,
        "state": "blocked" if blockers else "reconciled",
        "inputs": {
            "run": {"path": str(run_path), "sha256": "sha256:" + _hash_file(run_path)},
            "schema": {
                "path": str(schema_path),
                "sha256": "sha256:" + _hash_file(schema_path),
            },
            "roles_config": {
                "path": str(roles_path),
                "sha256": "sha256:" + _hash_file(roles_path),
            },
            "source": source_record,
            "results": [
                {
                    "role_id": role_id,
                    "path": str(path),
                    "sha256": "sha256:" + _hash_file(path),
                }
                for role_id, path, _ in result_records
            ],
            "snapshots": snapshot_records,
        },
        "outputs": output_bindings,
        "roles": [role_id for role_id, _, _ in result_records],
        "transformations": {
            "snapshot_aliases": snapshot_mappings,
            "id_mappings": id_mappings,
            "locator_mappings": locator_mappings,
            "source_path_mappings": source_path_mappings,
            "evidence_transformations": evidence_transformations,
            "status_rule": "preserve_producer_status_without_promotion",
        },
        "blockers": blockers,
        "warnings": warnings,
        "counts": {
            "mechanisms": len(mechanism_registry),
            "unknowns": len(unknown_registry),
            "drift_findings": len(drift_registry),
            "blockers": len(blockers),
            "warnings": len(warnings),
            "roles": len(result_records),
        },
    }
    report = {**unsigned_report, "report_hash": canonical_hash(unsigned_report)}
    atomic_create_json(targets["report"], report)
    return report
