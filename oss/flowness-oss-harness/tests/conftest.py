from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _allow_legacy_unit_focus(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Keep pre-W0 unit tests focused on their own contracts.

    Production code never reads this fixture. New execution-policy tests opt out
    and exercise the real fail-closed gate.
    """

    if request.node.get_closest_marker("execution_policy_enforced") is None:
        from flowness_oss_harness import cli, controller

        monkeypatch.setattr(
            controller,
            "require_agent_execution_allowed",
            lambda operation: ({"test_only": True}, "sha256:test"),
        )
        monkeypatch.setattr(
            cli,
            "require_command_execution_allowed",
            lambda command: ({"test_only": True}, "sha256:test"),
        )
