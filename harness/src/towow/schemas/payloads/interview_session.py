"""M-1.x L1 session-start checkpoint payloads (T0.1 — DOGFOOD-001-E repair).

# spec source:
#   04-l1-intelligence/M-1.1-interview-skill-detailed-design.md §4.1 5 类 checkpoint envelope
#   04-l1-intelligence/M-1.2-engineering-consensus §13.12 session-start checkpoint
#   04-l1-intelligence/M-1.3-planning §13 session-start checkpoint
#   06-l3-engineering-shell/M-3.1-cli-engineering-shell-detailed-design.md §4.3.1 interview start
#
# DOGFOOD-RUN-001 → DOGFOOD-001-E raw-prompt-entry-gap surfaced 2026-05-22:
#   spec §4.3.1 did not define a way to bring the user's *original* request into
#   InterviewSessionStarted as canonical payload. T0.1 fixes by:
#     1. Adding `--raw-prompt TEXT` option to `towow interview start` CLI
#     2. Defining InterviewSessionStartedPayload here so the raw prompt round-trips
#        into the canonical event payload (not lost in CLI stdout / stub-rewrap)
#
# RUN-031 T-L0-01 (write-boundary typed-payload validation §5.3.1):
#   The 3 L1 session-start checkpoints (interview / consensus / planner) are emitted
#   via Path A as real event_types (RUN-027 块4) but their CLI emit attaches a `runbook`
#   text field on top of the typed payload. To make these event_types enforceable at the
#   write boundary (PAYLOAD_REGISTRY → fail-closed), the payload schemas model the runbook
#   field faithfully, and consensus/planner get their own (previously missing) payload
#   classes so the registry truly covers every event_type.
#
# /work slash command (DOGFOOD-001-A repair) relies on this: /work runs
# `towow interview start --raw-prompt "..."` so user input becomes canonical event.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(strict=True, extra="forbid", frozen=True)


class InterviewSessionStartedPayload(BaseModel):
    """M-1.1 §4.1 session_start checkpoint payload.

    Mandatory fields:
      - session_id: the interview session this event opens (recorded in the
        per-session SessionLockRegistry work-card
        .towow/locks/sessions/interview/<session_id>.json plus its .meta sidecar;
        T0.2 session_id inheritance reads that registry — RUN-080 / T-SL-C
        retired the old single global session-pointer lock)
      - skill_id: always "M-1.1" for v3 interview skill
      - raw_prompt: the user's original natural-language request (DOGFOOD-001-E
        repair — must be canonical, not session-local)

    Optional:
      - parent_session_id: when interview is spawned as sub-session (e.g. fix
        triggered re-interview); None at top-level
      - runbook: human-readable operating note attached to the session-start node
        (CLI `_emit_node_runbook`); modeled so the write boundary can enforce this
        event_type's payload shape (RUN-031 T-L0-01).
    """

    model_config = _STRICT

    kind: Literal["InterviewSessionStarted"] = "InterviewSessionStarted"
    session_id: str = Field(min_length=1, pattern=r"^sess-interview-[A-Za-z0-9_-]+$")
    skill_id: Literal["M-1.1"] = "M-1.1"
    raw_prompt: str = Field(min_length=1)
    parent_session_id: str | None = None
    runbook: str | None = None


class EngineeringConsensusSessionStartedPayload(BaseModel):
    """M-1.2 §13.12 session_start checkpoint payload (RUN-027 块4 Path A real emit).

    Faithful to the CLI emit at `cli/main.py` consensus-start: kind / session_id /
    skill_id / runbook. RUN-031 T-L0-01 authored this previously-missing schema so the
    write-boundary PAYLOAD_REGISTRY covers ENGINEERING_CONSENSUS_SESSION_STARTED.
    """

    model_config = _STRICT

    kind: Literal["EngineeringConsensusSessionStarted"] = "EngineeringConsensusSessionStarted"
    session_id: str = Field(min_length=1)
    skill_id: Literal["M-1.2"] = "M-1.2"
    runbook: str | None = None


class PlannerSessionStartedPayload(BaseModel):
    """M-1.3 §13 session_start checkpoint payload (RUN-027 块4 Path A real emit).

    Faithful to the CLI emit at `cli/main.py` plan-start: kind / session_id / skill_id /
    consensus_event_id / plan_id / runbook. RUN-031 T-L0-01 authored this previously-missing
    schema so the write-boundary PAYLOAD_REGISTRY covers PLANNER_SESSION_STARTED.
    """

    model_config = _STRICT

    kind: Literal["PlannerSessionStarted"] = "PlannerSessionStarted"
    session_id: str = Field(min_length=1)
    skill_id: Literal["M-1.3"] = "M-1.3"
    consensus_event_id: str | None = None
    plan_id: str | None = None
    runbook: str | None = None


__all__ = [
    "EngineeringConsensusSessionStartedPayload",
    "InterviewSessionStartedPayload",
    "PlannerSessionStartedPayload",
]
