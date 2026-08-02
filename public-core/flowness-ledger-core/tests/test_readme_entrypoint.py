from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
QUICKSTART = ROOT / "docs" / "ALPHA_QUICKSTART_CANDIDATE.md"


def test_readme_has_one_unambiguous_public_alpha_entrypoint() -> None:
    """Keep the README from regressing to a host-Python dependent entry path."""

    document = README.read_text(encoding="utf-8")
    quickstart = QUICKSTART.read_text(encoding="utf-8")

    assert "## Quickstart" in document
    assert "[Alpha quickstart](docs/ALPHA_QUICKSTART_CANDIDATE.md)" in document
    assert "Python 3.11 or newer" in " ".join(document.split())
    assert "PYTHONPATH=src" not in document
    assert "private candidate" not in document.lower()
    assert "PYTHON_BIN=\"${PYTHON_BIN:-python3.12}\"" in quickstart
    assert "Python 3.11+ is required" in quickstart
    assert "cd public-core/flowness-ledger-core" in quickstart
    assert "Linux aarch64 with CPython 3.12" in quickstart
    assert "private staging" not in quickstart.lower()
    assert "keep the output private" not in quickstart.lower()
