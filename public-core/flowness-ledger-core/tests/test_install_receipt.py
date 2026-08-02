from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "attest_wheel_install.py"


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def test_local_wheel_install_receipt_records_and_rechecks_installed_candidate(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    wheel = Path(_run(sys.executable, str(ROOT / "tools" / "build_wheel.py"), "--output", str(dist)).stdout.strip())
    venv = tmp_path / "venv"
    _run(sys.executable, "-m", "venv", str(venv))
    python = venv / "bin" / "python"
    _run(str(python), "-m", "pip", "install", "--no-index", str(wheel))
    console_script = venv / "bin" / "flowness-ledger-demo"
    receipt = tmp_path / "receipt.json"
    created = _run(
        sys.executable,
        str(TOOL),
        "--create",
        "--receipt",
        str(receipt),
        "--wheel",
        str(wheel),
        "--python",
        str(python),
        "--console-script",
        str(console_script),
        "--demo-dir",
        str(tmp_path / "demo"),
    )
    result = json.loads(created.stdout)
    assert result["boundary"] == "public_open_alpha_local_wheel_observation_not_clean_room_receipt"
    assert result["environment"]["version"] == "1.0.0a1"
    assert result["demo"]["manifest_sha256"].startswith("sha256:")
    verified = _run(sys.executable, str(TOOL), "--verify", "--receipt", str(receipt))
    assert json.loads(verified.stdout) == result


def test_local_wheel_install_receipt_verifier_rejects_tampered_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"not":"a receipt"}\n', encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(TOOL), "--verify", "--receipt", str(receipt)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "invalid shape" in completed.stderr
