from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = ROOT / "docs" / "ALPHA_QUICKSTART_CANDIDATE.md"


def test_alpha_quickstart_names_current_public_install_and_cli_contract() -> None:
    """Keep the public walkthrough tied to the local tools it invokes."""

    document = QUICKSTART.read_text(encoding="utf-8")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["requires-python"] == ">=3.11"
    assert metadata["project"]["scripts"] == {
        "flowness-ledger-demo": "flowness_ledger_core.cli:main"
    }
    assert (ROOT / "tools" / "build_wheel.py").is_file()
    assert (ROOT / "tools" / "attest_wheel_install.py").is_file()
    assert (ROOT / "src" / "flowness_ledger_core" / "cli.py").is_file()

    required_fragments = {
        "Python 3.11 or newer",
        "Linux aarch64 with CPython 3.12",
        "cd public-core/flowness-ledger-core",
        "PYTHON_BIN=\"${PYTHON_BIN:-python3.12}\"",
        "\"$PYTHON_BIN\" tools/build_wheel.py --output \"$WHEEL_DIR\"",
        "pip install --no-index \"$WHEEL\"",
        "flowness_ledger_core-1.0.0a1-py3-none-any.whl",
        "\"$VENV_DIR/bin/flowness-ledger-demo\" --demo-dir \"$DEMO_DIR\"",
        "\"$VENV_DIR/bin/flowness-ledger-demo\" --verify-demo-dir \"$DEMO_DIR\"",
        "Linux aarch64 / CPython 3.12 clean-room result",
        "Those records are pending",
        "does not establish distributed consensus",
    }
    assert all(fragment in document for fragment in required_fragments)
    forbidden_fragments = {
        "private staging candidate",
        "local_private_candidate_not_independent_clean_room_or_public_release",
        "Keep the output private",
        "not a license grant",
        "not a public release",
    }
    assert not any(fragment.lower() in document.lower() for fragment in forbidden_fragments)
