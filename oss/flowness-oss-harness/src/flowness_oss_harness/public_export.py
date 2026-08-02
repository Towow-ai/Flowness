from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .models import utc_now
from .registry import ValidationError

DEFAULT_MAX_FILE_BYTES = 1024 * 1024
HARD_MAX_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 20 * 1024 * 1024
HARD_MAX_TOTAL_BYTES = 100 * 1024 * 1024
EXPORT_MANIFEST_NAME = "export-manifest.json"

DENIED_EXACT_PARTS = {
    ".git",
    ".towow",
    ".claude",
    ".codex",
    ".ssh",
    "auth.json",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "private",
    "internal",
}
DENIED_SUFFIXES = {
    ".env",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
}
DENIED_NAME_PATTERN = re.compile(
    r"(?i)(?:^|[._-])(?:secret|credential|private|internal)s?(?:[._-]|$)"
)
PRIVATE_CONTENT_PATTERNS = (
    (
        "private-key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ),
    (
        "known-secret-token",
        re.compile(
            r"\b(?:sk-ant|sk-proj|gh[pousr]|xox[baprs])-[A-Za-z0-9_-]{10,}\b"
        ),
    ),
    (
        "aws-access-key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "authorization-header",
        re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+"),
    ),
    (
        "embedded-credential",
        re.compile(
            r"""(?ix)
            \b(?:password|passwd|api[_-]?key|access[_-]?token|private[_-]?token)
            \s*[:=]\s*["'][^"'\r\n]{8,}["']
            """
        ),
    ),
    (
        "credential-url",
        re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
    ),
    (
        "private-classification",
        re.compile(
            r"(?i)\b(?:confidential|internal only|do not distribute|proprietary)\b"
            r"|内部资料|机密|不得外传"
        ),
    ),
    (
        "private-home-path",
        re.compile(r"(?:/Users|/home)/[^/\s]+/"),
    ),
)
LFS_POINTER_PREFIX = "version https://git-lfs.github.com/spec/v1"


@dataclass(frozen=True)
class ExportFile:
    source_path: str
    destination_path: str
    license: str
    reviewer: str
    source_mode: str
    blob_id: str
    content: bytes
    sha256: str


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
        raise ValidationError(stderr.strip() or "git command failed")
    return completed.stdout


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _clean_scalar(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or len(value) > 200
    ):
        raise ValidationError(f"{field} must be a non-empty single-line string")
    return value


def _relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValidationError(f"{field} must be a canonical POSIX relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValidationError(f"{field} must be a canonical POSIX relative path")
    return value


def _reject_denied_path(path: str, field: str) -> None:
    for part in PurePosixPath(path).parts:
        lowered = part.lower()
        if (
            lowered in DENIED_EXACT_PARTS
            or lowered.startswith(".env")
            or any(lowered.endswith(suffix) for suffix in DENIED_SUFFIXES)
            or DENIED_NAME_PATTERN.search(part)
        ):
            raise ValidationError(f"{field} contains denied private path: {path}")


def _positive_int(
    value: Any,
    field: str,
    default: int,
    hard_limit: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if value < 1 or value > hard_limit:
        raise ValidationError(f"{field} must be between 1 and {hard_limit}")
    return value


def _tree_entry(repo: Path, commit: str, source_path: str) -> tuple[str, str]:
    raw = _git(
        repo,
        "ls-tree",
        "-z",
        commit,
        "--",
        source_path,
        text=False,
    )
    assert isinstance(raw, bytes)
    rows = [row for row in raw.split(b"\0") if row]
    if len(rows) != 1:
        raise ValidationError(f"allowlisted source is missing or ambiguous: {source_path}")
    try:
        metadata, encoded_name = rows[0].split(b"\t", 1)
        mode, object_type, blob_id = metadata.decode("ascii").split()
        name = encoded_name.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError(f"invalid Git tree entry: {source_path}") from exc
    if name != source_path:
        raise ValidationError(f"Git tree path mismatch: {source_path}")
    if mode == "120000":
        raise ValidationError(f"symlink exports are forbidden: {source_path}")
    if object_type != "blob" or mode not in {"100644", "100755"}:
        raise ValidationError(f"source is not a regular file: {source_path}")
    return mode, blob_id


def _validate_content(source_path: str, content: bytes, max_bytes: int) -> str:
    if len(content) > max_bytes:
        raise ValidationError(
            f"allowlisted source exceeds max bytes ({max_bytes}): {source_path}"
        )
    if b"\0" in content:
        raise ValidationError(f"binary source is forbidden: {source_path}")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"non-UTF-8 source is forbidden: {source_path}") from exc
    if text.startswith(LFS_POINTER_PREFIX):
        raise ValidationError(f"Git LFS pointer is not exportable: {source_path}")
    for label, pattern in PRIVATE_CONTENT_PATTERNS:
        if pattern.search(text):
            raise ValidationError(
                f"private content pattern {label} found in: {source_path}"
            )
    return text


def _load_allowlist(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValidationError("allowlist manifest must be a regular file")
    raw = path.read_bytes()
    if len(raw) > DEFAULT_MAX_FILE_BYTES:
        raise ValidationError("allowlist manifest is oversized")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("allowlist manifest must be UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("allowlist manifest must be a JSON object")
    if payload.get("schema_version") != "public-export-allowlist/v1":
        raise ValidationError("unsupported allowlist schema_version")
    if not isinstance(payload.get("files"), list) or not payload["files"]:
        raise ValidationError("allowlist manifest must contain a non-empty files array")
    return payload, raw


def _collect_files(
    repo: Path,
    commit: str,
    allowlist: dict[str, Any],
) -> list[ExportFile]:
    default_max = _positive_int(
        allowlist.get("max_file_bytes"),
        "max_file_bytes",
        DEFAULT_MAX_FILE_BYTES,
        HARD_MAX_FILE_BYTES,
    )
    max_total = _positive_int(
        allowlist.get("max_total_bytes"),
        "max_total_bytes",
        DEFAULT_MAX_TOTAL_BYTES,
        HARD_MAX_TOTAL_BYTES,
    )
    files: list[ExportFile] = []
    source_paths: set[str] = set()
    destinations: set[str] = set()
    portable_destinations: set[str] = set()
    total_bytes = 0
    for index, row in enumerate(allowlist["files"]):
        if not isinstance(row, dict):
            raise ValidationError(f"files[{index}] must be an object")
        source_path = _relative_path(row.get("source"), f"files[{index}].source")
        destination_path = _relative_path(
            row.get("destination", source_path),
            f"files[{index}].destination",
        )
        _reject_denied_path(source_path, f"files[{index}].source")
        _reject_denied_path(destination_path, f"files[{index}].destination")
        portable_destination = unicodedata.normalize(
            "NFC", destination_path
        ).casefold()
        if portable_destination == EXPORT_MANIFEST_NAME.casefold():
            raise ValidationError("destination collides with export manifest")
        if source_path in source_paths:
            raise ValidationError(f"duplicate allowlisted source: {source_path}")
        if destination_path in destinations:
            raise ValidationError(f"duplicate export destination: {destination_path}")
        if portable_destination in portable_destinations:
            raise ValidationError(
                f"export destinations collide on a portable filesystem: "
                f"{destination_path}"
            )
        if any(
            portable_destination.startswith(f"{existing}/")
            or existing.startswith(f"{portable_destination}/")
            for existing in portable_destinations
        ):
            raise ValidationError(
                f"export destinations have a file/directory collision: "
                f"{destination_path}"
            )
        source_paths.add(source_path)
        destinations.add(destination_path)
        portable_destinations.add(portable_destination)
        license_name = _clean_scalar(row.get("license"), f"files[{index}].license")
        reviewer = _clean_scalar(row.get("reviewer"), f"files[{index}].reviewer")
        file_max = _positive_int(
            row.get("max_bytes"),
            f"files[{index}].max_bytes",
            default_max,
            min(default_max, HARD_MAX_FILE_BYTES),
        )
        source_mode, blob_id = _tree_entry(repo, commit, source_path)
        raw_size = _git(repo, "cat-file", "-s", blob_id)
        assert isinstance(raw_size, str)
        try:
            size_bytes = int(raw_size.strip())
        except ValueError as exc:
            raise ValidationError(
                f"Git returned an invalid blob size: {source_path}"
            ) from exc
        if size_bytes > file_max:
            raise ValidationError(
                f"allowlisted source exceeds max bytes ({file_max}): {source_path}"
            )
        if total_bytes + size_bytes > max_total:
            raise ValidationError(
                f"allowlisted export exceeds max_total_bytes ({max_total})"
            )
        content = _git(repo, "cat-file", "blob", blob_id, text=False)
        assert isinstance(content, bytes)
        _validate_content(source_path, content, file_max)
        if len(content) != size_bytes:
            raise ValidationError(f"Git blob size changed while reading: {source_path}")
        total_bytes += len(content)
        files.append(
            ExportFile(
                source_path=source_path,
                destination_path=destination_path,
                license=license_name,
                reviewer=reviewer,
                source_mode=source_mode,
                blob_id=blob_id,
                content=content,
                sha256=_sha256(content),
            )
        )
    return files


def seal_public_export(
    source_repo: Path,
    source_ref: str,
    allowlist_manifest: Path,
    target: Path,
) -> dict[str, Any]:
    if source_repo.is_symlink():
        raise ValidationError("source repository cannot be a symlink")
    source_repo = source_repo.resolve()
    if not source_repo.is_dir():
        raise ValidationError("source repository must be a directory")
    source_ref = _clean_scalar(source_ref, "source_ref")
    if source_ref.startswith("-") or "\x00" in source_ref:
        raise ValidationError("source_ref is unsafe")

    target = target.expanduser()
    if target.exists() or target.is_symlink():
        raise ValidationError("export target already exists; refusing to overwrite")
    target_parent = target.parent.resolve()
    if not target_parent.is_dir():
        raise ValidationError("export target parent must already exist")
    target = target_parent / target.name
    if target == source_repo or source_repo in target.parents:
        raise ValidationError("export target must be outside the source repository")

    status_before = _git(
        source_repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    assert isinstance(status_before, str)
    if status_before.strip():
        raise ValidationError("source repository is dirty")
    commit = _git(source_repo, "rev-parse", "--verify", f"{source_ref}^{{commit}}")
    assert isinstance(commit, str)
    commit = commit.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValidationError("source_ref did not resolve to a commit")

    allowlist_manifest = allowlist_manifest.expanduser()
    if allowlist_manifest.is_symlink():
        raise ValidationError("allowlist manifest cannot be a symlink")
    allowlist, allowlist_raw = _load_allowlist(allowlist_manifest.resolve())
    repository_label = _clean_scalar(
        allowlist.get("source_repository", source_repo.name),
        "source_repository",
    )
    files = _collect_files(source_repo, commit, allowlist)
    status_after = _git(
        source_repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    assert isinstance(status_after, str)
    if status_after != status_before:
        raise ValidationError("source repository changed during export preflight")
    if target.exists() or target.is_symlink():
        raise ValidationError("export target appeared during preflight")

    target.mkdir(mode=0o755, exist_ok=False)
    records: list[dict[str, Any]] = []
    for item in files:
        destination = target / PurePosixPath(item.destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(item.content)
            handle.flush()
            os.fsync(handle.fileno())
        destination.chmod(0o755 if item.source_mode == "100755" else 0o644)
        records.append(
            {
                "source_path": item.source_path,
                "destination_path": item.destination_path,
                "source_ref": source_ref,
                "source_commit": commit,
                "source_blob": item.blob_id,
                "source_mode": item.source_mode,
                "license": item.license,
                "sha256": item.sha256,
                "reviewer": item.reviewer,
                "size_bytes": len(item.content),
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": "sealed-public-export/v1",
        "source_repository": repository_label,
        "source_ref": source_ref,
        "source_commit": commit,
        "allowlist_sha256": _sha256(allowlist_raw),
        "sealed_at": utc_now(),
        "files": records,
        "counts": {
            "files": len(records),
            "bytes": sum(record["size_bytes"] for record in records),
        },
        "boundary": (
            "This manifest proves the exported bytes came from the named Git "
            "commit and passed local deny rules. It does not grant publication "
            "approval or prove license ownership."
        ),
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    manifest_path = target / EXPORT_MANIFEST_NAME
    with manifest_path.open("xb") as handle:
        handle.write(manifest_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(target, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return manifest
