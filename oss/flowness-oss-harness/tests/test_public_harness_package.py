from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
POLICY = REPO / "oss/flowness-oss-harness/config/open-alpha-package-scope.json"
FORBIDDEN_WHEEL_PARTS = (
    "account_registry.py",
    "account_rotation.py",
    "claude_bg_helper.py",
    "run_owned_agent.py",
    "transcript_efficiency.py",
    "owner_session_interaction.py",
    "bg_worktree_poller.py",
    "glue/settings.json",
    "skills/downtime-recovery/anchors/README.md",
    "skills/fix-self-check/anchors/n1-ep-b-unilateral-flag-flip.md",
    "skills/fix-self-check/anchors/p1-fork-verdict-exemplars.md",
    "skills/handoff/anchors/regression-anchors.md",
    "skills/meta-review/anchors/regression-anchors.md",
)


def _classify(relative: str, rules: list[dict[str, object]]) -> str:
    for rule in rules:
        if any(fnmatch.fnmatchcase(relative, pattern) for pattern in rule["patterns"]):
            return str(rule["disposition"])
    raise AssertionError(f"unclassified package path: {relative}")


def _selected_harness_tree(target: Path) -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    harness_roots = [path for path in policy["scope_roots"] if path.startswith("harness/")]
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", *harness_roots],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for relative in sorted(set(tracked)):
        if _classify(relative, policy["rules"]) != "include":
            continue
        destination = target / Path(relative).relative_to("harness")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, destination)


def test_public_harness_readme_keeps_cleanroom_as_an_external_exact_release_gate() -> None:
    readme = (REPO / "harness/README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())
    assert "cannot self-authorize that claim" in normalized
    assert "external immutable release record binds the exact commit" in normalized
    assert "acceptance passed on that coordinate" not in readme


def test_public_metadata_and_supply_chain_are_exact() -> None:
    harness = tomllib.loads((REPO / "harness/pyproject.toml").read_text())
    oss = tomllib.loads((REPO / "oss/flowness-oss-harness/pyproject.toml").read_text())
    ledger = tomllib.loads((REPO / "public-core/flowness-ledger-core/pyproject.toml").read_text())
    assert (harness["project"]["name"], harness["project"]["version"], harness["project"]["license"]) == (
        "flowness-harness", "1.0.0a1", "Apache-2.0"
    )
    assert harness["project"]["scripts"] == {"flowness-harness": "towow.open_alpha_cli:main"}
    assert (oss["project"]["name"], oss["project"]["version"], oss["project"]["license"]) == (
        "flowness-oss-harness", "1.0.0a1", "Apache-2.0"
    )
    assert oss["project"]["scripts"] == {"flowness-oss": "flowness_oss_harness.public_cli:main"}

    package_contract = json.loads(
        (REPO / "public-core/flowness-ledger-core/open-alpha-public-package-metadata.json").read_text()
    )
    package_rows = {item["distribution_name"]: item for item in package_contract["packages"]}
    project_inputs = {
        "flowness-harness": ("harness/pyproject.toml", harness),
        "flowness-oss-harness": ("oss/flowness-oss-harness/pyproject.toml", oss),
        "flowness-ledger-core": ("public-core/flowness-ledger-core/pyproject.toml", ledger),
    }
    assert package_contract["package_state"] == "three_exact_installable_source_metadata_files_present"
    assert set(package_rows) == set(project_inputs)
    observed_entrypoints = set()
    for distribution_name, (relative, pyproject) in project_inputs.items():
        row = package_rows[distribution_name]
        project = pyproject["project"]
        license_expression = project["license"]
        if isinstance(license_expression, dict):
            license_expression = license_expression["text"]
        metadata_path = REPO / relative
        assert row["source_metadata"] == relative
        assert row["source_metadata_sha256"] == "sha256:" + hashlib.sha256(
            metadata_path.read_bytes()
        ).hexdigest()
        assert row["version"] == project["version"]
        assert row["requires_python"] == project["requires-python"]
        assert row["license_expression"] == license_expression
        assert row["runtime_dependencies"] == project.get("dependencies", [])
        assert row["build_dependencies"] == pyproject["build-system"]["requires"]
        assert row["entry_points"] == project["scripts"]
        assert row["assembly_state"] == "exact_installable_source_metadata_present"
        observed_entrypoints.update(row["entry_points"])
    assert observed_entrypoints == {"flowness-harness", "flowness-oss", "flowness-ledger-demo"}

    sbom = json.loads((REPO / "harness/sbom.cdx.json").read_text())
    ledger_sbom = json.loads((REPO / "public-core/flowness-ledger-core/sbom.cdx.json").read_text())
    pins = {}
    for line in (REPO / "harness/open-alpha-requirements.lock").read_text().splitlines():
        if "==" in line and not line.startswith("#"):
            name, tail = line.split("==", 1)
            pins[name.lower().replace("_", "-")] = tail.split(";", 1)[0].strip()
    components = {item["name"]: item["version"] for item in sbom["components"]}
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.6"
    assert ledger_sbom == sbom
    assert components == pins
    properties = {item["name"]: item["value"] for item in sbom["metadata"]["properties"]}
    assert properties["flowness:sbom-state"] == "locked-transitive-candidate"
    assert properties["flowness:release-authorized"] == "false"
    assert properties["flowness:offline-wheelhouse"] == "not-sealed-cross-platform"
    build_lock = REPO / "harness/build-system-requirements.lock"
    assert properties["flowness:build-system-lock-sha256"] == hashlib.sha256(
        build_lock.read_bytes()
    ).hexdigest()
    resolution = package_contract["resolution"]
    assert resolution == {
        "state": "exact_unified_transitive_candidate",
        "unified_lock": "harness/open-alpha-requirements.lock",
        "unified_lock_sha256": "sha256:20842e38c9a067c33a6d7cb2b5f8c500822b54f7c52f36c27af76dfa00e86468",
        "source_locks": [
            "harness/uv.lock",
            "oss/flowness-oss-harness/uv.lock",
            "harness/build-system-requirements.lock",
        ],
        "source_lock_sha256": {
            "harness/uv.lock": "sha256:d4daefe66ed39e0d2603da0928fa4b9fd9c0c4a6c80ee468ef067b025c305c28",
            "oss/flowness-oss-harness/uv.lock": "sha256:b10b61342fa57d7f1988a314a8840f945768c3a786cc8732c1b043432813994f",
            "harness/build-system-requirements.lock": "sha256:192507377d4e349d6bea7b00ef2f140e235ccdae267d73612e3835f4c20ca4da",
        },
        "canonical_sbom": "harness/sbom.cdx.json",
        "canonical_sbom_sha256": "sha256:288095753f9b9b0acd2341568c1ea486de90d51a7d3c4568a897d16173fe37d3",
        "component_sbom_mirror": "public-core/flowness-ledger-core/sbom.cdx.json",
        "component_sbom_mirror_sha256": "sha256:288095753f9b9b0acd2341568c1ea486de90d51a7d3c4568a897d16173fe37d3",
        "sbom_state": "locked-transitive-candidate",
    }
    assert resolution["unified_lock_sha256"] == "sha256:" + hashlib.sha256(
        (REPO / resolution["unified_lock"]).read_bytes()
    ).hexdigest()
    for relative, expected_hash in resolution["source_lock_sha256"].items():
        assert expected_hash == "sha256:" + hashlib.sha256((REPO / relative).read_bytes()).hexdigest()
    for path_key, hash_key in (
        ("canonical_sbom", "canonical_sbom_sha256"),
        ("component_sbom_mirror", "component_sbom_mirror_sha256"),
    ):
        assert resolution[hash_key] == "sha256:" + hashlib.sha256(
            (REPO / resolution[path_key]).read_bytes()
        ).hexdigest()
    source_pins = set()
    for relative in resolution["source_locks"]:
        source = REPO / relative
        if relative.endswith("uv.lock"):
            lock = tomllib.loads(source.read_text())
            source_pins.update(
                (item["name"].lower().replace("_", "-"), str(item["version"]))
                for item in lock["package"]
            )
        else:
            for line in source.read_text().splitlines():
                if "==" in line and not line.startswith("#"):
                    name, version = line.split("==", 1)
                    source_pins.add((name.lower().replace("_", "-"), version.strip()))
    assert set(pins.items()) <= source_pins


def test_installed_oss_wheel_exposes_only_working_public_commands(tmp_path: Path) -> None:
    source = REPO / "oss/flowness-oss-harness"
    package = tmp_path / "oss-package"
    package.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(source / name, package / name)
    for name in ("src", "config", "schemas"):
        shutil.copytree(source / name, package / name)

    wheel_dir = tmp_path / "oss-dist"
    wheel_dir.mkdir()
    original = Path.cwd()
    os.chdir(package)
    try:
        from setuptools.build_meta import build_wheel

        wheel_name = build_wheel(str(wheel_dir))
    finally:
        os.chdir(original)

    venv = tmp_path / "oss-venv"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    console = venv / ("Scripts/flowness-oss.exe" if os.name == "nt" else "bin/flowness-oss")
    dependency_paths = [path for path in sys.path if "site-packages" in path]
    env = {
        **os.environ,
        "PIP_NO_INDEX": "1",
        "PYTHONNOUSERSITE": "1",
        # Cross-platform wheelhouse coverage is a Beta Unknown; this targeted
        # Alpha test supplies already-installed locked dependencies without a
        # network call so it can verify the newly built console wheel itself.
        "PYTHONPATH": os.pathsep.join(dependency_paths),
    }
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel_dir / wheel_name)],
        check=True,
        env=env,
        capture_output=True,
    )
    command_table = subprocess.run(
        [str(console), "commands"], check=True, env=env, capture_output=True, text=True
    )
    assert set(json.loads(command_table.stdout)["commands"]) == {
        "commands", "open-alpha-demo", "open-alpha-demo-inspect"
    }
    version_probe = subprocess.run(
        [str(python), "-c", "import flowness_oss_harness as p; print(p.__version__)"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert version_probe.stdout.strip() == "1.0.0a1"
    legacy = subprocess.run(
        [str(python), "-m", "flowness_oss_harness.cli", "--help"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert legacy.returncode == 2
    assert "legacy orchestration commands are not public APIs" in legacy.stderr
    assert "run-wave" not in legacy.stdout + legacy.stderr
    run_root = tmp_path / "public-demo"
    subprocess.run(
        [str(console), "open-alpha-demo", "--output", str(run_root)],
        check=True,
        env=env,
        capture_output=True,
    )
    inspected = subprocess.run(
        [str(console), "open-alpha-demo-inspect", "--run-root", str(run_root)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    summary = json.loads(inspected.stdout)
    assert summary["producer_agents"] == 3
    assert summary["judge_agents_per_round"] == 2
    assert summary["round_1"] == "blocked"
    assert summary["blocker_id"] == "BLK-DEMO-TRUTH-001"
    assert summary["targeted_rework"] == "verified"
    assert summary["round_2"] == "accepted"
    trace = json.loads((run_root / "trace.json").read_text(encoding="utf-8"))
    assert trace["rounds"][0]["blocking_report_ids"] == ["report-r1-judge-truth"]
    assert trace["rounds"][1]["blocking_report_ids"] == []
    rejected = subprocess.run(
        [str(console), "init"], env=env, capture_output=True, text=True
    )
    assert rejected.returncode == 2
    assert "Open Alpha exposes only" in rejected.stderr


def test_selected_export_wheel_excludes_private_modules_and_installs_offline(tmp_path: Path) -> None:
    package = tmp_path / "harness"
    package.mkdir()
    _selected_harness_tree(package)
    assert (package / "pyproject.toml").is_file()
    assert (package / "src/towow/open_alpha_cli.py").is_file()
    assert (package / "src/towow/l2/reflow_sentinel.py").is_file()
    for forbidden in FORBIDDEN_WHEEL_PARTS:
        assert not any(path.as_posix().endswith(forbidden) for path in package.rglob("*"))

    wheel_dir = tmp_path / "dist"
    wheel_dir.mkdir()
    original = Path.cwd()
    os.chdir(package)
    try:
        from setuptools.build_meta import build_wheel

        wheel_name = build_wheel(str(wheel_dir))
    finally:
        os.chdir(original)
    wheel = wheel_dir / wheel_name
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "towow/open_alpha_cli.py" in names
        assert "towow/skills/review/SKILL.md" in names
        assert "towow/glue/agents/skills/work/SKILL.md" in names
        for resource in (
            "towow/glue/codex/hooks/pre_tool_use_adapter.py",
            "towow/glue/hooks/PreToolUse-guard.sh",
            "towow/glue/hooks/Stop-detect-confirmation-loop.py",
            "towow/glue/hooks/UserPromptSubmit-inbound-check.py",
        ):
            assert resource in names
        assert not any(name.endswith(forbidden) for forbidden in FORBIDDEN_WHEEL_PARTS for name in names)
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
        assert "Name: flowness-harness\n" in metadata
        assert "Version: 1.0.0a1\n" in metadata
        assert "License-Expression: Apache-2.0\n" in metadata

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = {**os.environ, "PIP_NO_INDEX": "1", "PYTHONNOUSERSITE": "1"}
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        check=True,
        env=env,
        capture_output=True,
    )
    completed = subprocess.run(
        [str(python), "-m", "towow.open_alpha_cli", "--json"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["version"] == "1.0.0a1"
    version_probe = subprocess.run(
        [str(python), "-c", "import towow; print(towow.__version__)"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert version_probe.stdout.strip() == "1.0.0a1"
