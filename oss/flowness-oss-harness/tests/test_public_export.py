from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from flowness_oss_harness.cli import main
from flowness_oss_harness.public_export import seal_public_export
from flowness_oss_harness.registry import ValidationError


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit(repo: Path) -> str:
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return _git(repo, "rev-parse", "HEAD")


def _allowlist(tmp_path: Path, files: list[dict], **extra: object) -> Path:
    path = tmp_path / "allowlist.json"
    payload = {
        "schema_version": "public-export-allowlist/v1",
        "source_repository": "Harness",
        "files": files,
        **extra,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _entry(source: str, destination: str | None = None) -> dict:
    row = {
        "source": source,
        "license": "Apache-2.0",
        "reviewer": "reviewer-a",
    }
    if destination is not None:
        row["destination"] = destination
    return row


def test_sealed_export_copies_only_allowlisted_commit_blobs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    app = repo / "src" / "app.py"
    app.write_text("def run():\n    return 'public'\n", encoding="utf-8")
    app.chmod(0o755)
    (repo / "README.md").write_text("# Public runtime\n", encoding="utf-8")
    commit = _commit(repo)
    allowlist = _allowlist(
        tmp_path,
        [
            _entry("src/app.py"),
            _entry("README.md", "docs/README.md"),
        ],
    )
    target = tmp_path / "public-stage"

    result = seal_public_export(repo, "HEAD", allowlist, target)

    assert (target / "src" / "app.py").read_bytes() == app.read_bytes()
    assert (target / "docs" / "README.md").read_text() == "# Public runtime\n"
    assert os.stat(target / "src" / "app.py").st_mode & 0o111
    assert result["source_commit"] == commit
    assert result["counts"]["files"] == 2
    record = next(
        item for item in result["files"] if item["source_path"] == "src/app.py"
    )
    assert record["source_ref"] == "HEAD"
    assert record["license"] == "Apache-2.0"
    assert record["reviewer"] == "reviewer-a"
    assert record["sha256"] == hashlib.sha256(app.read_bytes()).hexdigest()
    written_manifest = json.loads(
        (target / "export-manifest.json").read_text(encoding="utf-8")
    )
    assert written_manifest == result


def test_cli_export_seal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _repo(tmp_path)
    (repo / "runtime.py").write_text("print('ready')\n", encoding="utf-8")
    _commit(repo)
    allowlist = _allowlist(tmp_path, [_entry("runtime.py")])
    target = tmp_path / "public-stage"

    returncode = main(
        [
            "export-seal",
            "--source-repo",
            str(repo),
            "--source-ref",
            "HEAD",
            "--allowlist",
            str(allowlist),
            "--target",
            str(target),
        ]
    )

    assert returncode == 0
    assert json.loads(capsys.readouterr().out)["counts"]["files"] == 1
    assert (target / "runtime.py").is_file()


def test_export_reads_the_explicit_commit_not_the_current_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = repo / "runtime.py"
    source.write_text("VERSION = 1\n", encoding="utf-8")
    first_commit = _commit(repo)
    source.write_text("VERSION = 2\n", encoding="utf-8")
    _commit(repo)
    target = tmp_path / "public-stage"

    result = seal_public_export(
        repo,
        first_commit,
        _allowlist(tmp_path, [_entry("runtime.py")]),
        target,
    )

    assert (target / "runtime.py").read_text(encoding="utf-8") == "VERSION = 1\n"
    assert result["source_commit"] == first_commit


def test_dirty_source_is_rejected_before_target_creation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    source = repo / "runtime.py"
    source.write_text("print('one')\n", encoding="utf-8")
    _commit(repo)
    source.write_text("print('two')\n", encoding="utf-8")
    target = tmp_path / "public-stage"

    with pytest.raises(ValidationError, match="source repository is dirty"):
        seal_public_export(
            repo,
            "HEAD",
            _allowlist(tmp_path, [_entry("runtime.py")]),
            target,
        )

    assert not target.exists()


def test_existing_target_is_never_overwritten(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "runtime.py").write_text("print('ready')\n", encoding="utf-8")
    _commit(repo)
    target = tmp_path / "public-stage"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValidationError, match="already exists"):
        seal_public_export(
            repo,
            "HEAD",
            _allowlist(tmp_path, [_entry("runtime.py")]),
            target,
        )

    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("source", "destination", "message"),
    [
        ("../private.txt", None, "canonical POSIX relative path"),
        ("runtime.py", "../escape.py", "canonical POSIX relative path"),
        (".env", None, "denied private path"),
        ("private/runtime.py", None, "denied private path"),
    ],
)
def test_path_escape_and_denylist_are_rejected(
    tmp_path: Path,
    source: str,
    destination: str | None,
    message: str,
) -> None:
    repo = _repo(tmp_path)
    (repo / "runtime.py").write_text("print('ready')\n", encoding="utf-8")
    (repo / ".env").write_text("placeholder\n", encoding="utf-8")
    (repo / "private").mkdir()
    (repo / "private" / "runtime.py").write_text("placeholder\n", encoding="utf-8")
    _commit(repo)
    target = tmp_path / "public-stage"

    with pytest.raises(ValidationError, match=message):
        seal_public_export(
            repo,
            "HEAD",
            _allowlist(tmp_path, [_entry(source, destination)]),
            target,
        )

    assert not target.exists()


def test_committed_symlink_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "runtime.py").write_text("print('ready')\n", encoding="utf-8")
    os.symlink("runtime.py", repo / "runtime-link.py")
    _commit(repo)
    target = tmp_path / "public-stage"

    with pytest.raises(ValidationError, match="symlink exports are forbidden"):
        seal_public_export(
            repo,
            "HEAD",
            _allowlist(tmp_path, [_entry("runtime-link.py")]),
            target,
        )

    assert not target.exists()


@pytest.mark.parametrize(
    ("content", "extra", "message"),
    [
        (b"abcde", {"max_file_bytes": 4}, "exceeds max bytes"),
        (b"abc\x00def", {}, "binary source is forbidden"),
        (
            b'API_KEY = "this-is-a-real-looking-secret"\n',
            {},
            "private content pattern embedded-credential",
        ),
        (
            b"CONFIDENTIAL - do not distribute\n",
            {},
            "private content pattern private-classification",
        ),
    ],
)
def test_oversized_binary_and_private_content_are_rejected(
    tmp_path: Path,
    content: bytes,
    extra: dict,
    message: str,
) -> None:
    repo = _repo(tmp_path)
    (repo / "runtime.txt").write_bytes(content)
    _commit(repo)
    target = tmp_path / "public-stage"

    with pytest.raises(ValidationError, match=message):
        seal_public_export(
            repo,
            "HEAD",
            _allowlist(tmp_path, [_entry("runtime.txt")], **extra),
            target,
        )

    assert not target.exists()


def test_allowlist_manifest_symlink_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "runtime.py").write_text("print('ready')\n", encoding="utf-8")
    _commit(repo)
    real_allowlist = _allowlist(tmp_path, [_entry("runtime.py")])
    linked_allowlist = tmp_path / "allowlist-link.json"
    os.symlink(real_allowlist.name, linked_allowlist)

    with pytest.raises(ValidationError, match="allowlist manifest cannot be a symlink"):
        seal_public_export(
            repo,
            "HEAD",
            linked_allowlist,
            tmp_path / "public-stage",
        )


def test_destination_file_directory_collision_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    _commit(repo)
    allowlist = _allowlist(
        tmp_path,
        [
            _entry("one.txt", "docs"),
            _entry("two.txt", "docs/two.txt"),
        ],
    )

    with pytest.raises(ValidationError, match="file/directory collision"):
        seal_public_export(
            repo,
            "HEAD",
            allowlist,
            tmp_path / "public-stage",
        )


def test_destination_case_collision_is_rejected_portably(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    _commit(repo)
    allowlist = _allowlist(
        tmp_path,
        [
            _entry("one.txt", "README.md"),
            _entry("two.txt", "readme.md"),
        ],
    )

    with pytest.raises(ValidationError, match="portable filesystem"):
        seal_public_export(
            repo,
            "HEAD",
            allowlist,
            tmp_path / "public-stage",
        )
