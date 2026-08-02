from __future__ import annotations

"""Freeze and verify a file-exact Flowness Open Alpha RC0 export.

The exporter consumes the canonical Open Alpha scope policy, but reads every
payload byte from the clean ``HEAD`` commit.  It never copies held or excluded
records and it cannot grant release authorization.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .open_alpha_package_scope import build_open_alpha_package_manifest
from .registry import ValidationError
from .resources import SCHEMAS_ROOT
from .schema_validation import load_validated_json, validate_payload


EXPORT_SCHEMA = SCHEMAS_ROOT / "rc0-export-manifest.schema.json"
FREEZE_SCHEMA = SCHEMAS_ROOT / "rc0-freeze-record.schema.json"
RIGHTS_POLICY_SCHEMA = SCHEMAS_ROOT / "rc0-rights-policy.schema.json"
EXPORT_SCHEMA_VERSION = "flowness-rc0-export-manifest/v1"
FREEZE_SCHEMA_VERSION = "flowness-rc0-freeze-record/v1"
EXPORT_MANIFEST_NAME = "OPEN_ALPHA_EXPORT_MANIFEST.json"
FREEZE_RECORD_NAME = "RC0_FREEZE_RECORD.json"
RIGHTS_POLICY_NAME = "OPEN_ALPHA_RIGHTS_POLICY.json"
_METADATA_NAMES = {EXPORT_MANIFEST_NAME, FREEZE_RECORD_NAME, RIGHTS_POLICY_NAME}


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        text=text,
    )
    if completed.returncode:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise ValidationError(stderr.strip() or "RC0-EXPORT-GIT-FAILED")
    return completed.stdout


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or ".." in pure.parts
        or value in _METADATA_NAMES
        or "\x00" in value
    ):
        raise ValidationError(f"RC0-EXPORT-PATH-INVALID:{value}")
    return pure


def _assert_repository_clean(repo: Path) -> Path:
    root = Path(str(_git(repo, "rev-parse", "--show-toplevel")).strip()).resolve(strict=True)
    status_output = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", text=False)
    assert isinstance(status_output, bytes)
    if status_output:
        raise ValidationError("RC0-EXPORT-REPOSITORY-DIRTY")
    return root


def _load_rights_policy(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("RC0-EXPORT-RIGHTS-POLICY-INVALID") from exc
    groups = payload.get("rights_groups") if isinstance(payload, dict) else None
    if not isinstance(groups, list) or not groups:
        raise ValidationError("RC0-EXPORT-RIGHTS-GROUPS-MISSING")
    validate_payload(payload, RIGHTS_POLICY_SCHEMA, "RC0 rights policy")
    for group in groups:
        required = {
            "group_id",
            "patterns",
            "license_expression",
            "origin_class",
            "rights_state",
            "ip_review_state",
            "provenance_state",
            "evidence_refs",
        }
        if not isinstance(group, dict) or set(group) != required:
            raise ValidationError("RC0-EXPORT-RIGHTS-GROUP-INVALID")
        if not isinstance(group["patterns"], list) or not group["patterns"]:
            raise ValidationError("RC0-EXPORT-RIGHTS-GROUP-INVALID")
        if not isinstance(group["license_expression"], str) or not group["license_expression"]:
            raise ValidationError("RC0-EXPORT-LICENSE-MAPPING-MISSING")
        for evidence_ref in group["evidence_refs"]:
            if not isinstance(evidence_ref, dict):
                raise ValidationError("RC0-EXPORT-RIGHTS-EVIDENCE-INVALID")
            _safe_relative(evidence_ref["path"])
    return payload


def _repository_file(repo: Path, path: Path, *, label: str) -> str:
    if path.is_symlink():
        raise ValidationError(f"RC0-EXPORT-{label}-SYMLINK")
    try:
        relative = path.resolve(strict=True).relative_to(repo).as_posix()
    except (OSError, ValueError) as exc:
        raise ValidationError(f"RC0-EXPORT-{label}-OUTSIDE-REPOSITORY") from exc
    _safe_relative(relative)
    return relative


def _rights_for(path: str, policy: dict[str, Any]) -> dict[str, str]:
    matches = [
        group
        for group in policy["rights_groups"]
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in group["patterns"])
    ]
    if len(matches) != 1:
        raise ValidationError(f"RC0-EXPORT-RIGHTS-MAPPING-NOT-EXACT:{path}")
    group = matches[0]
    return {
        "rights_group": group["group_id"],
        "license_expression": group["license_expression"],
        "rights_state": group["rights_state"],
        "ip_review_state": group["ip_review_state"],
    }


def _head_entry(repo: Path, head: str, path: str) -> tuple[str, str]:
    raw = _git(repo, "ls-tree", "-z", head, "--", path, text=False)
    assert isinstance(raw, bytes)
    entries = [entry for entry in raw.split(b"\0") if entry]
    if len(entries) != 1:
        raise ValidationError(f"RC0-EXPORT-HEAD-ENTRY-MISSING:{path}")
    try:
        metadata, encoded_path = entries[0].split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        observed_path = encoded_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError(f"RC0-EXPORT-HEAD-ENTRY-INVALID:{path}") from exc
    if observed_path != path or kind != "blob" or mode not in {"100644", "100755"}:
        raise ValidationError(f"RC0-EXPORT-NONREGULAR-HEAD-ENTRY:{path}")
    return mode, blob


def _write_exclusive(path: Path, value: bytes, mode: str = "100644") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o755 if mode == "100755" else 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_rc0_sealed_export(
    *,
    repo: Path,
    scope_policy_path: Path,
    rights_policy_path: Path,
    export_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Atomically create a new sealed export directory from clean ``HEAD``."""

    root = _assert_repository_clean(repo)
    export_root = export_root.resolve(strict=False)
    if export_root.exists() or export_root.is_symlink():
        raise ValidationError("RC0-EXPORT-TARGET-MUST-NOT-EXIST")
    export_root.parent.mkdir(parents=True, exist_ok=True)
    parent = export_root.parent.resolve(strict=True)

    scope_manifest = build_open_alpha_package_manifest(
        repo=root,
        policy_path=scope_policy_path.resolve(strict=True),
    )
    if scope_manifest.get("release_authorized") is not False:
        raise ValidationError("RC0-EXPORT-SCOPE-CANNOT-AUTHORIZE")
    if scope_manifest["dependency_closure"]["status"] != "closed":
        raise ValidationError("RC0-EXPORT-DEPENDENCY-CLOSURE-BLOCKED")
    if scope_manifest["consumer_closure"]["status"] != "closed":
        raise ValidationError("RC0-EXPORT-CONSUMER-CLOSURE-BLOCKED")
    head = scope_manifest["repository"]["head"]
    if str(_git(root, "rev-parse", "HEAD")).strip() != head:
        raise ValidationError("RC0-EXPORT-HEAD-DRIFT")
    rights_policy_relative = _repository_file(root, rights_policy_path, label="RIGHTS-POLICY")
    rights_mode, rights_blob = _head_entry(root, head, rights_policy_relative)
    if rights_mode != "100644":
        raise ValidationError("RC0-EXPORT-RIGHTS-POLICY-MODE-INVALID")
    rights_raw = _git(root, "cat-file", "blob", rights_blob, text=False)
    assert isinstance(rights_raw, bytes)
    rights_policy = _load_rights_policy(rights_raw)
    rights_policy_hash = _sha256_bytes(rights_raw)

    included = [record for record in scope_manifest["records"] if record["disposition"] == "include"]
    if not included:
        raise ValidationError("RC0-EXPORT-INCLUDE-EMPTY")
    files: list[dict[str, Any]] = []
    temp_root: Path | None = None
    try:
        temp_root = Path(tempfile.mkdtemp(prefix=f".{export_root.name}.tmp-", dir=parent))
        for record in included:
            path = record["path"]
            pure = _safe_relative(path)
            mode, blob = _head_entry(root, head, path)
            if record["git_blob_sha1"] != "sha1:" + blob:
                raise ValidationError(f"RC0-EXPORT-BLOB-DRIFT:{path}")
            raw = _git(root, "cat-file", "blob", blob, text=False)
            assert isinstance(raw, bytes)
            sha256 = _sha256_bytes(raw)
            if sha256 != record["sha256"] or len(raw) != record["bytes"]:
                raise ValidationError(f"RC0-EXPORT-BYTE-DRIFT:{path}")
            target = temp_root.joinpath(*pure.parts)
            try:
                target.relative_to(temp_root)
            except ValueError as exc:
                raise ValidationError(f"RC0-EXPORT-PATH-ESCAPE:{path}") from exc
            _write_exclusive(target, raw, mode)
            files.append(
                {
                    "path": path,
                    "git_mode": mode,
                    "git_blob_sha1": record["git_blob_sha1"],
                    "sha256": sha256,
                    "bytes": len(raw),
                    "maturity": record["maturity"],
                    "component": record["component"],
                    **_rights_for(path, rights_policy),
                }
            )

        files.sort(key=lambda item: item["path"])
        aggregate_hash = canonical_hash({"files": files})
        export_unsigned = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "export_id": "flowness-open-alpha-rc0-" + aggregate_hash.removeprefix("sha256:")[:24],
            "source_repository": {
                "commit": head,
                "tree": scope_manifest["repository"]["tree"],
            },
            "scope": {
                "manifest_id": scope_manifest["manifest_id"],
                "manifest_hash": scope_manifest["manifest_hash"],
                "policy_path": scope_manifest["policy"]["path"],
                "policy_sha256": scope_manifest["policy"]["sha256"],
            },
            "rights_policy": {
                "source_path": rights_policy_relative,
                "metadata_path": RIGHTS_POLICY_NAME,
                "sha256": rights_policy_hash,
            },
            "payload": {
                "files": len(files),
                "bytes": sum(item["bytes"] for item in files),
                "aggregate_hash": aggregate_hash,
            },
            "files": files,
            "release_authorized": False,
            "claim_boundary": "RC0 is a reproducible frozen candidate. It is not publication authorization, a rights clearance, a clean-room result, or a production claim.",
        }
        export_manifest = {**export_unsigned, "manifest_hash": canonical_hash(export_unsigned)}
        validate_payload(export_manifest, EXPORT_SCHEMA, "RC0 export manifest")

        freeze_unsigned = {
            "schema_version": FREEZE_SCHEMA_VERSION,
            "freeze_id": "rc0-" + canonical_hash(
                {"commit": head, "export_manifest_hash": export_manifest["manifest_hash"]}
            ).removeprefix("sha256:")[:24],
            "release_name": "Flowness Open Alpha RC0",
            "source_commit": head,
            "source_tree": scope_manifest["repository"]["tree"],
            "scope_manifest_hash": scope_manifest["manifest_hash"],
            "export_manifest_hash": export_manifest["manifest_hash"],
            "payload_aggregate_hash": aggregate_hash,
            "rights_policy_sha256": rights_policy_hash,
            "publication_state": "frozen_candidate_not_authorized",
            "release_authorized": False,
            "owner_approval_evidence": [],
            "next_required_gate": "sealed-export-clean-install-and-independent-jury",
        }
        freeze_record = {**freeze_unsigned, "record_hash": canonical_hash(freeze_unsigned)}
        validate_payload(freeze_record, FREEZE_SCHEMA, "RC0 freeze record")
        _write_exclusive(temp_root / RIGHTS_POLICY_NAME, rights_raw)
        _write_exclusive(temp_root / EXPORT_MANIFEST_NAME, _json_bytes(export_manifest))
        _write_exclusive(temp_root / FREEZE_RECORD_NAME, _json_bytes(freeze_record))
        os.replace(temp_root, export_root)
        temp_root = None
        return export_manifest, freeze_record
    finally:
        if temp_root is not None and temp_root.exists():
            shutil.rmtree(temp_root)


def verify_rc0_sealed_export(*, export_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = export_root.resolve(strict=True)
    if not root.is_dir() or export_root.is_symlink():
        raise ValidationError("RC0-VERIFY-ROOT-UNSAFE")
    for metadata_name in _METADATA_NAMES:
        metadata_path = root / metadata_name
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise ValidationError(f"RC0-VERIFY-METADATA-UNSAFE:{metadata_name}")
    manifest = load_validated_json(root / EXPORT_MANIFEST_NAME, EXPORT_SCHEMA, "RC0 export manifest")
    freeze = load_validated_json(root / FREEZE_RECORD_NAME, FREEZE_SCHEMA, "RC0 freeze record")
    verify_self_hash(manifest, "manifest_hash")
    verify_self_hash(freeze, "record_hash")
    if manifest["release_authorized"] is not False or freeze["release_authorized"] is not False:
        raise ValidationError("RC0-VERIFY-CANNOT-BE-AUTHORIZED")
    if freeze["publication_state"] != "frozen_candidate_not_authorized":
        raise ValidationError("RC0-VERIFY-PUBLICATION-STATE-INVALID")
    if freeze["export_manifest_hash"] != manifest["manifest_hash"]:
        raise ValidationError("RC0-VERIFY-FREEZE-MANIFEST-MISMATCH")
    rights_metadata_path = manifest["rights_policy"]["metadata_path"]
    if rights_metadata_path != RIGHTS_POLICY_NAME:
        raise ValidationError("RC0-VERIFY-RIGHTS-METADATA-PATH-INVALID")
    rights_raw = (root / rights_metadata_path).read_bytes()
    rights_hash = _sha256_bytes(rights_raw)
    if (
        rights_hash != manifest["rights_policy"]["sha256"]
        or rights_hash != freeze["rights_policy_sha256"]
    ):
        raise ValidationError("RC0-VERIFY-RIGHTS-POLICY-HASH-MISMATCH")
    rights_policy = _load_rights_policy(rights_raw)
    if (
        freeze["source_commit"] != manifest["source_repository"]["commit"]
        or freeze["source_tree"] != manifest["source_repository"]["tree"]
        or freeze["scope_manifest_hash"] != manifest["scope"]["manifest_hash"]
    ):
        raise ValidationError("RC0-VERIFY-FREEZE-SOURCE-MISMATCH")

    expected_paths = set(_METADATA_NAMES)
    paths = [record["path"] for record in manifest["files"]]
    if len(paths) != len(set(paths)):
        raise ValidationError("RC0-VERIFY-DUPLICATE-PAYLOAD-PATH")
    for record in manifest["files"]:
        pure = _safe_relative(record["path"])
        expected_paths.add(record["path"])
        path = root.joinpath(*pure.parts)
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"RC0-VERIFY-FILE-UNSAFE:{record['path']}")
        try:
            path.resolve(strict=True).relative_to(root)
        except ValueError as exc:
            raise ValidationError(f"RC0-VERIFY-PATH-ESCAPE:{record['path']}") from exc
        if _sha256_file(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValidationError(f"RC0-VERIFY-BYTE-MISMATCH:{record['path']}")
        actual_mode = stat.S_IMODE(path.stat().st_mode)
        expected_executable = record["git_mode"] == "100755"
        if bool(actual_mode & 0o111) != expected_executable:
            raise ValidationError(f"RC0-VERIFY-MODE-MISMATCH:{record['path']}")
        expected_rights = _rights_for(record["path"], rights_policy)
        observed_rights = {
            key: record[key]
            for key in ("rights_group", "license_expression", "rights_state", "ip_review_state")
        }
        if observed_rights != expected_rights:
            raise ValidationError(f"RC0-VERIFY-RIGHTS-BINDING-MISMATCH:{record['path']}")

    observed_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if observed_paths != expected_paths:
        extra = sorted(observed_paths - expected_paths)
        missing = sorted(expected_paths - observed_paths)
        raise ValidationError(
            "RC0-VERIFY-FILESET-MISMATCH:extra=" + ",".join(extra) + ";missing=" + ",".join(missing)
        )
    if canonical_hash({"files": manifest["files"]}) != manifest["payload"]["aggregate_hash"]:
        raise ValidationError("RC0-VERIFY-AGGREGATE-HASH-MISMATCH")
    if (
        manifest["payload"]["files"] != len(manifest["files"])
        or manifest["payload"]["bytes"] != sum(item["bytes"] for item in manifest["files"])
    ):
        raise ValidationError("RC0-VERIFY-PAYLOAD-SUMMARY-MISMATCH")
    if freeze["payload_aggregate_hash"] != manifest["payload"]["aggregate_hash"]:
        raise ValidationError("RC0-VERIFY-FREEZE-PAYLOAD-MISMATCH")
    return manifest, freeze


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m flowness_oss_harness.rc0_sealed_export")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--repo", required=True, type=Path)
    build.add_argument("--scope-policy", required=True, type=Path)
    build.add_argument("--rights-policy", required=True, type=Path)
    build.add_argument("--export-root", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--export-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            manifest, freeze = build_rc0_sealed_export(
                repo=args.repo,
                scope_policy_path=args.scope_policy,
                rights_policy_path=args.rights_policy,
                export_root=args.export_root,
            )
        else:
            manifest, freeze = verify_rc0_sealed_export(export_root=args.export_root)
        print(
            json.dumps(
                {
                    "export_id": manifest["export_id"],
                    "freeze_id": freeze["freeze_id"],
                    "files": manifest["payload"]["files"],
                    "aggregate_hash": manifest["payload"]["aggregate_hash"],
                    "release_authorized": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
