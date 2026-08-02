from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from flowness_oss_harness.open_alpha_package_scope import (
    _consumer_closure,
    build_open_alpha_package_manifest,
    verify_open_alpha_package_manifest,
    write_open_alpha_package_manifest,
)
from flowness_oss_harness.registry import ValidationError


ROOT = Path(__file__).resolve().parents[3]


def test_current_scope_excludes_superseded_candidate_legal_documents() -> None:
    policy = json.loads(
        (ROOT / "oss/flowness-oss-harness/config/open-alpha-package-scope.json").read_text(
            encoding="utf-8"
        )
    )
    obsolete = {
        "oss/flowness-oss-harness/LICENSE-POLICY-CANDIDATE.md",
        "oss/flowness-oss-harness/NOTICE-CANDIDATE.md",
        "oss/flowness-oss-harness/THIRD_PARTY-CANDIDATE.md",
    }
    assert obsolete <= set(policy["required_exclude_paths"])
    for path in obsolete:
        first_match = next(
            rule
            for rule in policy["rules"]
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule["patterns"])
        )
        assert first_match["rule_id"] == "exclude-obsolete-release-candidate-documents"
        assert first_match["disposition"] == "exclude"

    assert "Current fail-closed Open Alpha release scope" in policy["global_boundary"]
    assert "production reliability" in policy["global_boundary"]


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _write(repo: Path, relative: str, content: str = "fixture\n") -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "test@example.invalid")
    _run(repo, "config", "user.name", "Open Alpha Test")
    samples = {
        "public-core/flowness-ledger-core/src/ledger.py": "stable\n",
        "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py": "experimental\n",
        "oss/flowness-oss-harness/docs/content-graph.md": "content\n",
        "oss/flowness-oss-harness/docs/architecture-atlas-local-v0.md": "target\n",
        "oss/flowness-oss-harness/docs/open-alpha-package-scope-v0.md": "scope\n",
        "oss/flowness-oss-harness/deploy/run-worker.sh": "private\n",
        "oss/flowness-oss-harness/pyproject.toml": "held\n",
        "harness/src/towow/l2/account_registry.py": "private account adapter\n",
        "harness/src/towow/l2/orchestrator.py": "from towow.l2 import account_registry\n",
    }
    for path, content in samples.items():
        _write(repo, path, content)
    policy_path = repo / "oss/flowness-oss-harness/config/open-alpha-package-scope.json"
    policy = {
        "schema_version": "open-alpha-package-scope-policy/v1",
        "package_id": "fixture-open-alpha",
        "scope_roots": [
            "public-core/flowness-ledger-core",
            "oss/flowness-oss-harness",
            "harness/src/towow",
        ],
        "ignored_generated_paths": ["oss/flowness-oss-harness/registries/open-alpha-package-manifest-v0.json"],
        "rules": [
            {"rule_id": "private", "patterns": ["oss/flowness-oss-harness/deploy/**", "harness/src/towow/l2/account_*.py"], "maturity": "private_excluded", "disposition": "exclude", "component": "private_boundary", "reason": "private", "claim_boundary": "not exported"},
            {"rule_id": "stable", "patterns": ["public-core/flowness-ledger-core/**"], "maturity": "stable", "disposition": "include", "component": "ledger_core", "reason": "stable", "claim_boundary": "local only"},
            {"rule_id": "target", "patterns": ["oss/flowness-oss-harness/docs/architecture-atlas*.md"], "maturity": "design_target", "disposition": "include", "component": "architecture_d0_d9", "reason": "target", "claim_boundary": "not current"},
            {"rule_id": "canonical", "patterns": ["harness/src/towow/l2/orchestrator.py"], "maturity": "experimental", "disposition": "include", "component": "canonical_orchestration", "reason": "canonical code", "claim_boundary": "not portable"},
            {"rule_id": "harness", "patterns": ["oss/flowness-oss-harness/src/**"], "maturity": "experimental", "disposition": "include", "component": "multi_agent_harness", "reason": "code", "claim_boundary": "not runtime"},
            {"rule_id": "content", "patterns": ["oss/flowness-oss-harness/docs/content-graph.md"], "maturity": "experimental", "disposition": "include", "component": "evidence_content_machine", "reason": "content", "claim_boundary": "candidate"},
            {"rule_id": "packaging", "patterns": ["oss/flowness-oss-harness/config/open-alpha-package-scope.json", "oss/flowness-oss-harness/docs/open-alpha-package-scope-v0.md"], "maturity": "experimental", "disposition": "include", "component": "open_alpha_packaging", "reason": "scope", "claim_boundary": "proposal"},
            {"rule_id": "hold", "patterns": ["oss/flowness-oss-harness/**"], "maturity": "design_target", "disposition": "hold", "component": "open_alpha_packaging", "reason": "held", "claim_boundary": "not assembled"},
        ],
        "required_include_components": ["ledger_core", "multi_agent_harness", "evidence_content_machine", "architecture_d0_d9", "open_alpha_packaging", "canonical_orchestration"],
        "required_include_paths": [
            "public-core/flowness-ledger-core/src/ledger.py",
            "oss/flowness-oss-harness/src/flowness_oss_harness/controller.py",
            "oss/flowness-oss-harness/docs/content-graph.md",
            "oss/flowness-oss-harness/docs/architecture-atlas-local-v0.md",
            "oss/flowness-oss-harness/docs/open-alpha-package-scope-v0.md",
            "harness/src/towow/l2/orchestrator.py",
        ],
        "required_exclude_paths": [
            "oss/flowness-oss-harness/deploy/run-worker.sh",
            "harness/src/towow/l2/account_registry.py",
        ],
        "global_boundary": "proposal only",
    }
    _write(repo, policy_path.relative_to(repo).as_posix(), json.dumps(policy))
    _run(repo, "add", ".")
    _run(repo, "commit", "-qm", "fixture")
    return repo, policy_path


def test_manifest_is_file_exact_and_keeps_stable_experimental_target_and_private_separate(tmp_path: Path) -> None:
    repo, policy = _fixture(tmp_path)
    manifest = build_open_alpha_package_manifest(repo=repo, policy_path=policy)
    by_path = {item["path"]: item for item in manifest["records"]}

    assert by_path["public-core/flowness-ledger-core/src/ledger.py"]["maturity"] == "stable"
    assert by_path["oss/flowness-oss-harness/src/flowness_oss_harness/controller.py"]["maturity"] == "experimental"
    assert by_path["oss/flowness-oss-harness/docs/architecture-atlas-local-v0.md"]["maturity"] == "design_target"
    assert by_path["oss/flowness-oss-harness/deploy/run-worker.sh"]["disposition"] == "exclude"
    assert by_path["harness/src/towow/l2/account_registry.py"]["disposition"] == "exclude"
    assert by_path["oss/flowness-oss-harness/pyproject.toml"]["disposition"] == "hold"
    assert manifest["release_authorized"] is False
    assert manifest["rights_state"] == "unreviewed"
    assert manifest["summary"]["tracked_files"] == len(manifest["records"])
    assert manifest["dependency_closure"]["status"] == "blocked"
    assert manifest["dependency_closure"]["blockers"] == [
        {
            "blocker_id": manifest["dependency_closure"]["blockers"][0]["blocker_id"],
            "importer_path": "harness/src/towow/l2/orchestrator.py",
            "imported_module": "towow.l2.account_registry",
            "target_path": "harness/src/towow/l2/account_registry.py",
            "target_disposition": "exclude",
            "required_action": "provide-public-adapter-or-include-reviewed-dependency",
        }
    ]
    assert manifest["consumer_closure"]["status"] == "closed"
    assert manifest["consumer_closure"]["blockers"] == []


def test_consumer_closure_rejects_markdown_and_json_edges_to_nonexported_files(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _write(repo, "docs/public.md", "[dead](../held/private.md)\n")
    _write(
        repo,
        "registries/public.json",
        json.dumps(
            {
                "schema_version": "mechanism-card-registry/v1",
                "source_inputs": {"source": {"path": "excluded/evidence.json"}},
                "edge": {"evidence_locator": "held/private.md#proof"},
                "claims": [
                    {"evidence_bindings": [{"path": "held/claim-test.py"}]}
                ],
                "cards": [
                    {
                        "static_coordinates": {
                            "test": [{"path": "held/mechanism-test.py"}],
                            "recovery": [
                                {
                                    "evidence_id": "withheld:coordinate-fixture",
                                    "sha256": "sha256:" + "0" * 64,
                                    "availability": "withheld_from_open_alpha",
                                }
                            ],
                        },
                        "why_it_exists": {
                            "anchors": [
                                {
                                    "current": {"path": "held/history.py"},
                                    "evolution": {
                                        "evidence_id": "withheld:history-fixture",
                                        "sha256": "sha256:" + "1" * 64,
                                        "availability": "withheld_from_open_alpha",
                                    },
                                }
                            ]
                        },
                        "mechanism_semantic_contract": {
                            "objects": [
                                {"coordinate": {"path": "held/object.py"}}
                            ],
                            "state_chain": {
                                "transitions": [
                                    {
                                        "evidence": {
                                            "source_coordinate": {"path": "held/edge.py"},
                                            "target_coordinate": {
                                                "evidence_id": "withheld:edge-fixture",
                                                "sha256": "sha256:" + "2" * 64,
                                                "availability": "withheld_from_open_alpha",
                                            },
                                        }
                                    }
                                ]
                            },
                        },
                    }
                ],
            }
        ),
    )
    _write(repo, "held/private.md", "held\n")
    for path in (
        "held/claim-test.py",
        "held/mechanism-test.py",
        "held/history.py",
        "held/object.py",
        "held/edge.py",
    ):
        _write(repo, path, "held\n")
    _write(repo, "excluded/evidence.json", "{}\n")
    records = [
        {"path": "docs/public.md", "disposition": "include"},
        {"path": "registries/public.json", "disposition": "include"},
        {"path": "held/private.md", "disposition": "hold"},
        {"path": "held/claim-test.py", "disposition": "hold"},
        {"path": "held/mechanism-test.py", "disposition": "hold"},
        {"path": "held/history.py", "disposition": "hold"},
        {"path": "held/object.py", "disposition": "hold"},
        {"path": "held/edge.py", "disposition": "hold"},
        {"path": "excluded/evidence.json", "disposition": "exclude"},
    ]

    closure = _consumer_closure(repo=repo, records=records)

    assert closure["status"] == "blocked"
    assert {
        (item["consumer_path"], item["reference_field"], item["target_path"])
        for item in closure["blockers"]
    } == {
        ("docs/public.md", "markdown_link", "held/private.md"),
        ("registries/public.json", "evidence_locator", "held/private.md"),
        ("registries/public.json", "path", "excluded/evidence.json"),
        ("registries/public.json", "claims.evidence_bindings.path", "held/claim-test.py"),
        ("registries/public.json", "static_coordinates.path", "held/mechanism-test.py"),
        ("registries/public.json", "history_anchor.path", "held/history.py"),
        ("registries/public.json", "semantic_object.coordinate.path", "held/object.py"),
        ("registries/public.json", "semantic_edge.coordinate.path", "held/edge.py"),
    }


def test_consumer_closure_rejects_stale_hash_for_included_json_source(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source_path = "registries/source.json"
    target_path = "config/policy.json"
    _write(repo, target_path, '{"policy": true}\n')
    _write(
        repo,
        source_path,
        json.dumps(
            {
                "source_inputs": {
                    "scope_policy": {
                        "path": target_path,
                        "sha256": "sha256:" + "0" * 64,
                    }
                }
            }
        ),
    )
    digest = lambda path: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    records = [
        {
            "path": source_path,
            "disposition": "include",
            "sha256": digest(repo / source_path),
        },
        {
            "path": target_path,
            "disposition": "include",
            "sha256": digest(repo / target_path),
        },
    ]
    closure = _consumer_closure(repo=repo, records=records)

    assert closure["status"] == "blocked"
    assert len(closure["blockers"]) == 1
    assert closure["blockers"][0]["reference_field"] == "path+sha256"
    assert closure["blockers"][0]["target_path"] == target_path
    assert closure["blockers"][0]["target_disposition"] == "include_hash_mismatch"
    assert closure["blockers"][0]["required_action"] == (
        "refresh-hash-bound-reference-to-exact-included-bytes"
    )


def test_manifest_verifier_detects_byte_drift_and_private_include_attack(tmp_path: Path) -> None:
    repo, policy = _fixture(tmp_path)
    output = tmp_path / "manifest.json"
    write_open_alpha_package_manifest(repo=repo, policy_path=policy, output=output)
    assert verify_open_alpha_package_manifest(repo=repo, policy_path=policy, manifest_path=output)

    target = repo / "public-core/flowness-ledger-core/src/ledger.py"
    target.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="WORKTREE-DIRTY"):
        verify_open_alpha_package_manifest(repo=repo, policy_path=policy, manifest_path=output)

    _run(repo, "restore", "--", "public-core/flowness-ledger-core/src/ledger.py")
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["rules"][0]["maturity"] = "experimental"
    payload["rules"][0]["disposition"] = "include"
    policy.write_text(json.dumps(payload), encoding="utf-8")
    _run(repo, "add", "oss/flowness-oss-harness/config/open-alpha-package-scope.json")
    _run(repo, "commit", "-qm", "private include attack")
    with pytest.raises(ValidationError, match="PRIVATE-INCLUDE"):
        build_open_alpha_package_manifest(repo=repo, policy_path=policy)


def test_required_canonical_anchor_cannot_silently_fall_to_hold(tmp_path: Path) -> None:
    repo, policy = _fixture(tmp_path)
    payload = json.loads(policy.read_text(encoding="utf-8"))
    payload["rules"][1]["disposition"] = "hold"
    policy.write_text(json.dumps(payload), encoding="utf-8")
    _run(repo, "add", "oss/flowness-oss-harness/config/open-alpha-package-scope.json")
    _run(repo, "commit", "-qm", "hide canonical anchor")
    with pytest.raises(ValidationError, match="REQUIRED-INCLUDE-VIOLATION"):
        build_open_alpha_package_manifest(repo=repo, policy_path=policy)


def test_required_canonical_anchor_cannot_disappear_from_tracked_scope(tmp_path: Path) -> None:
    repo, policy = _fixture(tmp_path)
    _run(repo, "rm", "harness/src/towow/l2/orchestrator.py")
    _run(repo, "commit", "-qm", "remove canonical anchor")
    with pytest.raises(ValidationError, match="REQUIRED-PATH-MISSING"):
        build_open_alpha_package_manifest(repo=repo, policy_path=policy)


def test_required_exclude_path_may_be_absent_from_sanitized_public_tree(tmp_path: Path) -> None:
    repo, policy = _fixture(tmp_path)
    excluded = [
        "oss/flowness-oss-harness/deploy/run-worker.sh",
        "harness/src/towow/l2/account_registry.py",
    ]
    _run(repo, "rm", *excluded)
    _run(repo, "commit", "-qm", "sanitize public replacement tree")

    manifest = build_open_alpha_package_manifest(repo=repo, policy_path=policy)
    observed = {item["path"] for item in manifest["records"]}
    assert not (set(excluded) & observed)
    assert manifest["required_path_assertions"]["exclude"] == sorted(excluded)


def test_private_history_agent_anchors_are_held_out_of_public_scope() -> None:
    policy = json.loads(
        (ROOT / "oss/flowness-oss-harness/config/open-alpha-package-scope.json").read_text(
            encoding="utf-8"
        )
    )
    private_anchors = [
        ".claude/skills/downtime-recovery/anchors/README.md",
        ".claude/skills/fix-self-check/anchors/n1-ep-b-unilateral-flag-flip.md",
        ".claude/skills/fix-self-check/anchors/p1-fork-verdict-exemplars.md",
        ".claude/skills/handoff/anchors/regression-anchors.md",
        ".claude/skills/meta-review/anchors/regression-anchors.md",
        "harness/src/towow/skills/downtime-recovery/anchors/README.md",
        "harness/src/towow/skills/fix-self-check/anchors/n1-ep-b-unilateral-flag-flip.md",
        "harness/src/towow/skills/fix-self-check/anchors/p1-fork-verdict-exemplars.md",
        "harness/src/towow/skills/handoff/anchors/regression-anchors.md",
        "harness/src/towow/skills/meta-review/anchors/regression-anchors.md",
    ]

    for path in private_anchors:
        rule = next(
            item
            for item in policy["rules"]
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in item["patterns"])
        )
        assert rule["rule_id"] == "hold-private-history-agent-anchors"
        assert rule["disposition"] == "hold"


def test_public_repository_release_layout_is_required_and_included() -> None:
    policy = json.loads(
        (ROOT / "oss/flowness-oss-harness/config/open-alpha-package-scope.json").read_text(
            encoding="utf-8"
        )
    )
    release_paths = {
        "README.md",
        "LICENSE",
        "NOTICE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "MIGRATION.md",
        ".github/workflows/ci.yml",
    }

    assert "release_layout" in policy["required_include_components"]
    assert release_paths <= set(policy["scope_roots"])
    assert release_paths <= set(policy["required_include_paths"])
    for path in release_paths:
        matches = [
            rule
            for rule in policy["rules"]
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule["patterns"])
        ]
        assert matches[0]["rule_id"] == "include-public-repository-release-layout"
        assert matches[0]["disposition"] == "include"


def _first_scope_rule(policy: dict, path: str) -> dict:
    return next(
        rule
        for rule in policy["rules"]
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule["patterns"])
    )


def test_public_quickstart_docs_are_included_but_obsolete_route_stays_held() -> None:
    policy = json.loads(
        (ROOT / "oss/flowness-oss-harness/config/open-alpha-package-scope.json").read_text(
            encoding="utf-8"
        )
    )
    current_docs = {
        "oss/flowness-oss-harness/docs/open-alpha-demo.md",
        "oss/flowness-oss-harness/docs/source-policy.md",
    }
    assert current_docs <= set(policy["required_include_paths"])
    for path in current_docs:
        rule = _first_scope_rule(policy, path)
        assert rule["rule_id"] == "include-mechanism-content-drift-and-jury-docs"
        assert rule["disposition"] == "include"

    obsolete = "oss/flowness-oss-harness/docs/oss-module-route-v0.md"
    assert obsolete not in policy["required_include_paths"]
    obsolete_rule = _first_scope_rule(policy, obsolete)
    assert obsolete_rule["rule_id"] == "hold-obsolete-private-staging-narrative-and-consumers"
    assert obsolete_rule["disposition"] == "hold"


def test_exported_oss_tests_equal_public_ci_allowlist_and_have_source_closure() -> None:
    policy = json.loads(
        (ROOT / "oss/flowness-oss-harness/config/open-alpha-package-scope.json").read_text(
            encoding="utf-8"
        )
    )
    tests_root = ROOT / "oss/flowness-oss-harness/tests"
    source_root = ROOT / "oss/flowness-oss-harness/src"
    relative_tests = {
        path.relative_to(ROOT).as_posix(): path for path in tests_root.glob("test_*.py")
    }
    exported = {
        path
        for path in relative_tests
        if _first_scope_rule(policy, path)["disposition"] == "include"
    }
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    selected_by_ci = set(
        re.findall(r"oss/flowness-oss-harness/tests/test_[a-z0-9_]+\.py", workflow)
    )
    assert exported == selected_by_ci

    public_test_support = {
        "oss/flowness-oss-harness/tests/conftest.py",
        "oss/flowness-oss-harness/tests/public_candidate_b_fixture.py",
    }
    for support_path in public_test_support:
        assert _first_scope_rule(policy, support_path)["disposition"] == "include"
        assert support_path in policy["required_include_paths"]

    # Collection happens from the sealed export, where held test modules do not
    # exist. Walk both CI tests and their explicit support modules and reject a
    # top-level local import unless that module is part of the public scope.
    collection_modules = {
        **relative_tests,
        **{
            path: ROOT / path
            for path in public_test_support
        },
    }
    test_module_paths = {
        path.stem: path.relative_to(ROOT).as_posix()
        for path in tests_root.glob("*.py")
    }
    for module_path in sorted(exported | public_test_support):
        tree = ast.parse(collection_modules[module_path].read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported_modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = [node.module]
            elif isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            for imported_module in imported_modules:
                local_path = test_module_paths.get(imported_module.split(".")[0])
                if local_path is None:
                    continue
                assert _first_scope_rule(policy, local_path)["disposition"] == "include", (
                    f"{module_path} imports held local test module {local_path}"
                )

    assert _first_scope_rule(
        policy, "oss/flowness-oss-harness/config/execution-policy.json"
    )["disposition"] == "exclude"

    for test_path in sorted(exported):
        tree = ast.parse(relative_tests[test_path].read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            elif isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            for module in imported:
                if not module.startswith("flowness_oss_harness"):
                    continue
                relative = Path(*module.split("."))
                candidates = (
                    source_root / (relative.as_posix() + ".py"),
                    source_root / relative / "__init__.py",
                )
                target = next((candidate for candidate in candidates if candidate.is_file()), None)
                assert target is not None, f"{test_path} imports missing {module}"
                target_relative = target.relative_to(ROOT).as_posix()
                assert _first_scope_rule(policy, target_relative)["disposition"] == "include", (
                    f"{test_path} imports non-exported {target_relative}"
                )

    formerly_broken = {
        "oss/flowness-oss-harness/tests/test_architecture_visual_atlas.py",
        "oss/flowness-oss-harness/tests/test_demo_provenance.py",
        "oss/flowness-oss-harness/tests/test_mechanism_cards.py",
        "oss/flowness-oss-harness/tests/test_media_material_kit.py",
        "oss/flowness-oss-harness/tests/test_pro_blind_review.py",
    }
    for path in formerly_broken:
        assert _first_scope_rule(policy, path)["disposition"] != "include"
