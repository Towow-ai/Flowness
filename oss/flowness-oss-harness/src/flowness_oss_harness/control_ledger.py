from __future__ import annotations

"""Immutable admission ledger for the standalone OSS harness.

The ledger deliberately has no mutable projection.  At this program's bounded
scale (100 budget units), replaying sealed admission records while holding one
local lock is simpler and safer than maintaining separate budget and attempt
state that can drift apart.
"""

import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .execution_policy import require_agent_execution_allowed
from .integrity import canonical_hash, verify_self_hash
from .models import utc_now
from .registry import ValidationError, atomic_create_json
from .resources import SCHEMAS_ROOT
from .schema_validation import validate_payload


LEDGER_SCHEMA = SCHEMAS_ROOT / "work-admission-ledger-entry.schema.json"
ADMISSION_SCHEMA = SCHEMAS_ROOT / "work-admission-card.schema.json"
ATTEMPT_SCHEMA = SCHEMAS_ROOT / "work-attempt-ledger-event.schema.json"
CONTROLLER_VERSION = "flowness-oss-harness/0.1.0"
_ENTRY_NAME = re.compile(r"^(?P<sequence>[0-9]{12})-(?P<admission>[0-9a-f]{64})\.json$")
_ATTEMPT_NAME = re.compile(r"^(?P<sequence>[0-9]{12})-(?P<event>[0-9a-f]{64})\.json$")
_COMPONENTS = (
    "adjudicated_root_cause_family",
    "sealed_canonical_consumer_id",
    "normalized_error_or_stack_signature_hash",
    "source_or_entry_coordinates",
)
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _regular_directory(path: Path, code: str) -> Path:
    if path.is_symlink():
        raise ValidationError(code)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValidationError(code) from exc
    if path.is_symlink() or not path.is_dir():
        raise ValidationError(code)
    return path.resolve()


def _ledger_root(workspace: Path) -> Path:
    root = workspace.resolve()
    control = root / "control"
    control = _regular_directory(control, "CONTROL-LEDGER-ROOT-INVALID")
    if control.parent != root:
        raise ValidationError("CONTROL-LEDGER-ROOT-ESCAPES-WORKSPACE")
    admissions = _regular_directory(
        control / "admissions", "CONTROL-LEDGER-ADMISSIONS-INVALID"
    )
    if admissions.parent != control:
        raise ValidationError("CONTROL-LEDGER-ADMISSIONS-ESCAPES-WORKSPACE")
    attempts = _regular_directory(
        control / "attempts", "CONTROL-LEDGER-ATTEMPTS-INVALID"
    )
    if attempts.parent != control:
        raise ValidationError("CONTROL-LEDGER-ATTEMPTS-ESCAPES-WORKSPACE")
    return control


@contextmanager
def _locked_ledger(workspace: Path) -> Iterator[Path]:
    control = _ledger_root(workspace)
    lock_path = control / "ledger.lock"
    if lock_path.is_symlink():
        raise ValidationError("CONTROL-LEDGER-LOCK-INVALID")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise ValidationError("CONTROL-LEDGER-LOCK-INVALID") from exc
    try:
        if lock_path.is_symlink() or not lock_path.is_file():
            raise ValidationError("CONTROL-LEDGER-LOCK-INVALID")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield control
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def initialize_control_ledger(workspace: Path) -> None:
    """Create only the bounded, append-only control surface.

    This function creates no admission and therefore cannot reserve budget or
    enable execution while the policy remains frozen.
    """

    with _locked_ledger(workspace):
        return None


def _entry_path(admissions: Path, sequence: int, admission_id: str) -> Path:
    return admissions / f"{sequence:012d}-{admission_id}.json"


def _attempt_path(attempts: Path, sequence: int, event_id: str) -> Path:
    return attempts / f"{sequence:012d}-{event_id}.json"


def _read_entries_locked(control: Path) -> list[dict[str, Any]]:
    admissions = control / "admissions"
    entries: list[dict[str, Any]] = []
    named = sorted(admissions.iterdir(), key=lambda path: path.name)
    for path in named:
        if path.is_symlink() or not path.is_file():
            raise ValidationError("BUDGET-LEDGER-CORRUPT")
        match = _ENTRY_NAME.fullmatch(path.name)
        if match is None:
            raise ValidationError("BUDGET-LEDGER-CORRUPT")
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError("BUDGET-LEDGER-CORRUPT") from exc
        validate_payload(entry, LEDGER_SCHEMA, "work admission ledger entry")
        validate_payload(entry["card"], ADMISSION_SCHEMA, "sealed work admission card")
        if entry.get("card_hash") != canonical_hash(entry["card"]):
            raise ValidationError("BUDGET-LEDGER-CORRUPT")
        if entry.get("sequence") != len(entries) + 1:
            raise ValidationError("BUDGET-LEDGER-CORRUPT")
        if int(match.group("sequence")) != entry["sequence"]:
            raise ValidationError("BUDGET-LEDGER-CORRUPT")
        if match.group("admission") != entry["admission_id"]:
            raise ValidationError("BUDGET-LEDGER-CORRUPT")
        verify_self_hash(entry, "entry_hash")
        expected_previous = entries[-1]["entry_hash"] if entries else None
        if entry.get("previous_entry_hash") != expected_previous:
            raise ValidationError("BUDGET-LEDGER-CORRUPT")
        entries.append(entry)
    return entries


def _read_attempts_locked(
    control: Path, entries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read the separate append-only attempt chain bound to admission hashes."""

    admissions = {entry["admission_id"]: entry for entry in entries}
    attempts_dir = control / "attempts"
    events: list[dict[str, Any]] = []
    for path in sorted(attempts_dir.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT")
        match = _ATTEMPT_NAME.fullmatch(path.name)
        if match is None:
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT")
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT") from exc
        validate_payload(event, ATTEMPT_SCHEMA, "work attempt ledger event")
        if event["sequence"] != len(events) + 1 or int(match.group("sequence")) != event["sequence"]:
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT")
        if match.group("event") != event["event_id"]:
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT")
        verify_self_hash(event, "event_hash")
        if event["previous_event_hash"] != (events[-1]["event_hash"] if events else None):
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT")
        admission = admissions.get(event["admission_id"])
        if admission is None or admission["entry_hash"] != event["admission_entry_hash"]:
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT")
        if admission["policy_hash"] != event["policy_hash"]:
            raise ValidationError("ATTEMPT-LEDGER-CORRUPT")
        events.append(event)
    return events


def _attempt_states(
    entries: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Replay attempts without ever mutating admission reservations."""

    states = {
        entry["admission_id"]: {"state": "admitted", "entry": entry, "event": None}
        for entry in entries
    }
    for event in events:
        state = states[event["admission_id"]]
        prior = state["state"]
        kind = event["event_type"]
        if kind == "started":
            if prior != "admitted":
                raise ValidationError("ATTEMPT-LEDGER-ILLEGAL-TRANSITION")
            state["state"] = "in_flight"
        elif kind == "stop_requested":
            if prior != "in_flight":
                raise ValidationError("ATTEMPT-LEDGER-ILLEGAL-TRANSITION")
            state["state"] = "stop_requested"
        elif kind == "terminal":
            outcome = event["payload"]["outcome"]
            if prior not in {"admitted", "in_flight", "stop_requested"}:
                raise ValidationError("ATTEMPT-LEDGER-ILLEGAL-TRANSITION")
            if outcome not in {"completed", "failed", "stopped"}:
                raise ValidationError("ATTEMPT-LEDGER-ILLEGAL-TRANSITION")
            if prior == "stop_requested" and outcome != "stopped":
                raise ValidationError("ATTEMPT-LEDGER-ILLEGAL-TRANSITION")
            state["state"] = outcome
        else:  # schema validation keeps this unreachable.
            raise ValidationError("ATTEMPT-LEDGER-ILLEGAL-TRANSITION")
        state["event"] = event
    return states


def _append_attempt_locked(
    control: Path,
    entries: list[dict[str, Any]],
    events: list[dict[str, Any]],
    admission_id: str,
    event_type: str,
    run_binding: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    admission = next((item for item in entries if item["admission_id"] == admission_id), None)
    if admission is None:
        raise ValidationError("ATTEMPT-ADMISSION-NOT-FOUND")
    event_seed = {
        "admission_id": admission_id,
        "admission_entry_hash": admission["entry_hash"],
        "event_type": event_type,
        "run_binding": run_binding,
        "payload": payload,
        "previous_event_hash": events[-1]["event_hash"] if events else None,
    }
    event_id = canonical_hash(event_seed)[len("sha256:") :]
    event = {
        "schema_version": "work-attempt-ledger-event/v1",
        "sequence": len(events) + 1,
        "event_id": event_id,
        "event_type": event_type,
        "admission_id": admission_id,
        "admission_entry_hash": admission["entry_hash"],
        "policy_hash": admission["policy_hash"],
        "controller_version": CONTROLLER_VERSION,
        "occurred_at": utc_now(),
        "run_binding": run_binding,
        "payload": payload,
        "previous_event_hash": events[-1]["event_hash"] if events else None,
    }
    event["event_hash"] = canonical_hash(event)
    validate_payload(event, ATTEMPT_SCHEMA, "work attempt ledger event")
    atomic_create_json(
        _attempt_path(control / "attempts", event["sequence"], event_id), event
    )
    return event


def _budget_wave_id(wave_id: str, budget: dict[str, Any]) -> str:
    if wave_id in {"W2A", "W2B", "W2C"}:
        return "W2"
    if wave_id == "FOUNDATION-001":
        supported = budget.get("supported_wave_id")
        if not isinstance(supported, str):
            raise ValidationError("ENABLEMENT-SUPPORTED-WAVE-REQUIRED")
        if supported == "reserve":
            raise ValidationError("ENABLEMENT-MAY-NOT-CONSUME-RESERVE")
        return supported
    return wave_id


def _is_nonempty_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _is_nonempty_artifact_refs(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("artifact_id"), str)
            and item["artifact_id"].strip()
            and isinstance(item.get("sha256"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"])
            for item in value
        )
    )


def _validate_card(card: dict[str, Any], policy: dict[str, Any]) -> None:
    validate_payload(card, ADMISSION_SCHEMA, "work admission card")
    required = set(policy["task_admission"]["required_fields"])
    missing = sorted(required - set(card))
    if missing:
        raise ValidationError("WORK-ADMISSION-MISSING-FIELDS: " + ", ".join(missing))
    if not _TASK_ID.fullmatch(card["task_id"]):
        raise ValidationError("WORK-ADMISSION-TASK-ID-INVALID")
    rework = card.get("rework_binding")
    if rework is not None:
        artifact = rework.get("case_artifact") if isinstance(rework, dict) else None
        if (
            not isinstance(artifact, dict)
            or artifact.get("artifact_id") != f"blocker-case:{rework.get('blocker_case_id')}"
            or artifact.get("sha256") != rework.get("case_hash")
            or artifact not in card["immutable_inputs"]
            or card["current_gate_or_blocker"] != rework.get("blocker_id")
            or not isinstance(card.get("blocker"), dict)
        ):
            raise ValidationError("REWORK-BINDING-INVALID")
    known_waves = {item["wave_id"] for item in policy["waves"]}
    if card["wave_id"] not in known_waves:
        raise ValidationError("WORK-ADMISSION-WAVE-UNKNOWN")
    if card["task_class"] not in policy["task_admission"]["task_classes"]:
        raise ValidationError("WORK-ADMISSION-CLASS-INVALID")
    if card["lane"] not in policy["budget"]["lane_floors"]:
        raise ValidationError("WORK-ADMISSION-LANE-INVALID")
    if card["budget"].get("units") != 1:
        raise ValidationError("WORK-ADMISSION-BUDGET-UNIT-INVALID")
    change_intent = card.get("change_intent")
    if not isinstance(change_intent, dict) or any(
        change_intent.get(key) not in {0, 1}
        for key in ("code_changes", "deployments", "meta_reviews")
    ):
        raise ValidationError("WORK-ADMISSION-CHANGE-INTENT-INVALID")
    charge_wave = _budget_wave_id(card["wave_id"], card["budget"])
    if charge_wave not in policy["budget"]["wave_units"]:
        raise ValidationError("WORK-ADMISSION-BUDGET-WAVE-INVALID")
    if card["task_class"] == "core":
        current_outputs = next(
            item["outputs"] for item in policy["waves"] if item["wave_id"] == card["wave_id"]
        )
        if card["primary_core_artifact"] not in set(current_outputs) | set(
            policy["north_star_artifacts"]
        ):
            raise ValidationError("CORE-PRIMARY-ARTIFACT-REQUIRED")
        allowed_delta = set(policy["core_value_metrics"]) | set(current_outputs)
        if not _is_nonempty_strings(card["expected_core_value_delta"]) or not set(
            card["expected_core_value_delta"]
        ).issubset(allowed_delta):
            raise ValidationError("CORE-VALUE-DELTA-REQUIRED")
    elif card["task_class"] == "enablement":
        if card["expected_core_value_delta"]:
            raise ValidationError("ENABLEMENT-CORE-DELTA-MUST-BE-ZERO")
        if card["wave_id"] != policy["foundation"]["foundation_id"]:
            raise ValidationError("ENABLEMENT-FOUNDATION-IDENTITY-REQUIRED")
        blocker = card.get("blocker")
        if not isinstance(blocker, dict):
            raise ValidationError("ENABLEMENT-BLOCKER-FINGERPRINT-REQUIRED")
        if not _is_nonempty_artifact_refs(
            blocker.get("degraded_path_impossible_evidence")
        ):
            raise ValidationError("ENABLEMENT-DEGRADED-PATH-EVIDENCE-REQUIRED")
    else:
        if not card.get("judge_identity", "").strip():
            raise ValidationError("JUDGE-INDEPENDENT-IDENTITY-REQUIRED")
        if not (
            card.get("judge_verdict_or_delta", "").strip()
            or _is_nonempty_strings(card["expected_core_value_delta"])
        ):
            raise ValidationError("JUDGE-VERDICT-OR-DELTA-REQUIRED")


def _fingerprint(blocker: dict[str, Any]) -> tuple[str, dict[str, str]]:
    components = blocker.get("components")
    if not isinstance(components, dict):
        raise ValidationError("BLOCKER-FINGERPRINT-COMPONENTS-REQUIRED")
    normalized = {key: components.get(key) for key in _COMPONENTS}
    if not all(isinstance(value, str) and value.strip() for value in normalized.values()):
        raise ValidationError("BLOCKER-FINGERPRINT-COMPONENTS-REQUIRED")
    return canonical_hash(normalized), normalized  # type: ignore[arg-type]


def _fold(entries: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    allocated = policy["budget"]["wave_units"]
    used_waves = {wave: 0 for wave in allocated}
    used_lanes = {lane: 0 for lane in policy["budget"]["lane_floors"]}
    fingerprints: dict[str, dict[str, Any]] = {}
    task_ids: dict[str, str] = {}
    enablement_used = 0
    for entry in entries:
        task_ids[entry["task_id"]] = entry["admission_id"]
        budget = entry["budget_reservation"]
        used_waves[budget["charged_wave_id"]] += budget["units"]
        if entry["task_class"] == "enablement":
            enablement_used += budget["units"]
        else:
            used_lanes[entry["lane"]] += budget["units"]
        blocker = entry.get("blocker")
        if blocker:
            fingerprint_id = blocker["fingerprint_id"]
            existing = fingerprints.setdefault(
                fingerprint_id,
                {
                    **blocker["imported_usage"],
                    "components": blocker["components"],
                },
            )
            existing["attempts"] += 1
            for key in ("code_changes", "deployments", "meta_reviews"):
                existing[key] += entry["change_intent"][key]
    return {
        "used_total": sum(used_waves.values()),
        "used_waves": used_waves,
        "used_lanes": used_lanes,
        "enablement_used": enablement_used,
        "fingerprints": fingerprints,
        "task_ids": task_ids,
    }


def _resolve_blocker(
    card: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any] | None:
    blocker = card.get("blocker")
    if blocker is None:
        return None
    fingerprint_id, components = _fingerprint(blocker)
    overlaps = [
        existing_id
        for existing_id, existing in state["fingerprints"].items()
        if sum(
            components[key] == existing["components"].get(key) for key in _COMPONENTS
        ) >= 2
    ]
    if len(overlaps) > 1:
        raise ValidationError("BLOCKER-FINGERPRINT-ADJUDICATION-REQUIRED")
    imported_usage = {"attempts": 0, "code_changes": 0, "deployments": 0, "meta_reviews": 0}
    if overlaps:
        fingerprint_id = overlaps[0]
    elif card["task_class"] != "enablement" or card["wave_id"] != "FOUNDATION-001":
        adjudication = blocker.get("new_fingerprint_adjudication")
        if not isinstance(adjudication, dict) or adjudication.get("adjudicator_identity") == card["role_id"]:
            raise ValidationError("BLOCKER-FINGERPRINT-UNADJUDICATED")
        evidence = adjudication.get("evidence")
        if not _is_nonempty_artifact_refs([evidence]) or evidence not in card["immutable_inputs"]:
            raise ValidationError("BLOCKER-FINGERPRINT-UNADJUDICATED")
    else:
        historical = blocker.get("historical_ledger_import")
        if not isinstance(historical, dict):
            raise ValidationError("FOUNDATION-LEDGER-IMPORT-REQUIRED")
        evidence = historical.get("evidence")
        if not _is_nonempty_artifact_refs([evidence]) or evidence not in card["immutable_inputs"]:
            raise ValidationError("FOUNDATION-LEDGER-IMPORT-REQUIRED")
        for key in imported_usage:
            if not isinstance(historical.get(key), int) or historical[key] < 0:
                raise ValidationError("FOUNDATION-LEDGER-IMPORT-REQUIRED")
            imported_usage[key] = historical[key]
    return {
        "fingerprint_id": fingerprint_id,
        "components": components,
        "imported_usage": imported_usage,
    }


def _validate_budget(
    card: dict[str, Any], state: dict[str, Any], policy: dict[str, Any]
) -> str:
    units = card["budget"]["units"]
    charged_wave = _budget_wave_id(card["wave_id"], card["budget"])
    if state["used_total"] + units > policy["budget"]["total_units"]:
        raise ValidationError("BUDGET-LEDGER-EXHAUSTED")
    if state["used_waves"][charged_wave] + units > policy["budget"]["wave_units"][charged_wave]:
        raise ValidationError("BUDGET-WAVE-EXHAUSTED")
    if card["task_class"] == "enablement":
        if state["enablement_used"] + units > policy["budget"]["enablement_units_max"]:
            raise ValidationError("ENABLEMENT-BUDGET-EXHAUSTED")
    else:
        lane_after = dict(state["used_lanes"])
        lane_after[card["lane"]] += units
        remaining_total = policy["budget"]["total_units"] - state["used_total"] - units
        remaining_floors = sum(
            max(policy["budget"]["lane_floors"][lane] - used, 0)
            for lane, used in lane_after.items()
        )
        if remaining_floors > remaining_total:
            raise ValidationError("BUDGET-LANE-FLOORS-WOULD-BE-STARVED")
    return charged_wave


def admit_work(workspace: Path, card: dict[str, Any]) -> dict[str, Any]:
    """Atomically validate and reserve one immutable unit of program work."""

    policy, policy_hash = require_agent_execution_allowed("work-admission")
    _validate_card(card, policy)
    card_hash = canonical_hash(card)
    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
        if any(entry["policy_hash"] != policy_hash for entry in entries):
            raise ValidationError("BUDGET-LEDGER-POLICY-HASH-MISMATCH")
        state = _fold(entries, policy)
        prior_id = state["task_ids"].get(card["task_id"])
        if prior_id:
            existing = next(entry for entry in entries if entry["admission_id"] == prior_id)
            if existing["card_hash"] == card_hash:
                return {
                    "state": "admitted",
                    "idempotent": True,
                    "admission_id": prior_id,
                    "entry_hash": existing["entry_hash"],
                }
            raise ValidationError("WORK-ADMISSION-TASK-ID-REUSED")
        blocker = _resolve_blocker(card, state)
        if blocker is not None:
            prior_usage = state["fingerprints"].get(
                blocker["fingerprint_id"], blocker["imported_usage"]
            )
            attempts = prior_usage["attempts"]
            if attempts >= policy["attempt_policy"]["targeted_attempts_per_fingerprint_lifetime_max"]:
                raise ValidationError("ATTEMPT-LIMIT-EXHAUSTED")
            limits = {
                "code_changes": policy["attempt_policy"][
                    "code_changes_per_fingerprint_lifetime_max"
                ],
                "deployments": policy["attempt_policy"][
                    "deployments_per_foundation_lifetime_max"
                ],
                "meta_reviews": policy["attempt_policy"][
                    "meta_reviews_per_fingerprint_lifetime_max"
                ],
            }
            for key, limit in limits.items():
                if prior_usage[key] + card["change_intent"][key] > limit:
                    raise ValidationError(f"{key.upper()}-LIFETIME-LIMIT-EXHAUSTED")
        elif any(card["change_intent"].values()):
            raise ValidationError("CHANGE-INTENT-REQUIRES-ADJUDICATED-BLOCKER")
        charged_wave = _validate_budget(card, state, policy)
        admission_id = canonical_hash({"policy_hash": policy_hash, "card": card})[
            len("sha256:") :
        ]
        entry = {
            "schema_version": "work-admission-ledger-entry/v1",
            "program_id": policy["program_id"],
            "sequence": len(entries) + 1,
            "admission_id": admission_id,
            "task_id": card["task_id"],
            "admitted_at": utc_now(),
            "policy_hash": policy_hash,
            "controller_version": CONTROLLER_VERSION,
            "card_hash": card_hash,
            "card": card,
            "task_class": card["task_class"],
            "wave_id": card["wave_id"],
            "lane": card["lane"],
            "role_id": card["role_id"],
            "budget_reservation": {"units": 1, "charged_wave_id": charged_wave},
            "change_intent": card["change_intent"],
            "blocker": blocker,
            "previous_entry_hash": entries[-1]["entry_hash"] if entries else None,
        }
        entry["entry_hash"] = canonical_hash(entry)
        target = _entry_path(control / "admissions", entry["sequence"], admission_id)
        atomic_create_json(target, entry)
    return {
        "state": "admitted",
        "idempotent": False,
        "admission_id": admission_id,
        "entry_hash": entry["entry_hash"],
    }


def load_admitted_work(
    workspace: Path, admission_ids: list[str], policy_hash: str
) -> list[dict[str, Any]]:
    """Return only current-policy permits named by an execution request.

    This is intentionally read-only.  A permit has already consumed its budget
    at admission time; `run_wave` merely proves that it is the permit bound to
    the exact sealed run and role that are about to start.
    """

    if not admission_ids or len(admission_ids) != len(set(admission_ids)):
        raise ValidationError("RUN-ADMISSION-IDS-INVALID")
    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
    by_id = {entry["admission_id"]: entry for entry in entries}
    missing = sorted(set(admission_ids) - set(by_id))
    if missing:
        raise ValidationError("RUN-ADMISSION-NOT-FOUND: " + ", ".join(missing))
    selected = [by_id[admission_id] for admission_id in admission_ids]
    if any(entry["policy_hash"] != policy_hash for entry in selected):
        raise ValidationError("RUN-ADMISSION-POLICY-HASH-MISMATCH")
    return selected


def _validate_run_binding(admission: dict[str, Any], binding: dict[str, Any]) -> None:
    required = {"run_id", "run_hash", "wave_id", "role_id"}
    if set(binding) != required:
        raise ValidationError("ATTEMPT-RUN-BINDING-INVALID")
    if binding["wave_id"] != admission["wave_id"] or binding["role_id"] != admission["role_id"]:
        raise ValidationError("ATTEMPT-RUN-BINDING-INVALID")
    run_id, run_hash = binding["run_id"], binding["run_hash"]
    if (run_id is None) != (run_hash is None):
        raise ValidationError("ATTEMPT-RUN-BINDING-INVALID")
    if run_id is not None:
        if not isinstance(run_id, str) or not isinstance(run_hash, str):
            raise ValidationError("ATTEMPT-RUN-BINDING-INVALID")
        expected = {"artifact_id": f"oss-run:{run_id}", "sha256": run_hash}
        if expected not in admission["card"]["immutable_inputs"]:
            raise ValidationError("ATTEMPT-RUN-BINDING-INVALID")


def _default_payload(reason_code: str) -> dict[str, Any]:
    return {
        "outcome": None,
        "reason_code": reason_code,
        "execution_record": None,
        "evidence": [],
        "retest_required": False,
    }


def claim_attempt(
    workspace: Path, admission_id: str, run_binding: dict[str, Any]
) -> dict[str, Any]:
    """Move exactly one admitted permit to in-flight before creating outputs."""

    _, current_policy_hash = require_agent_execution_allowed("attempt-claim")
    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
        events = _read_attempts_locked(control, entries)
        states = _attempt_states(entries, events)
        state = states.get(admission_id)
        if state is None:
            raise ValidationError("ATTEMPT-ADMISSION-NOT-FOUND")
        admission = state["entry"]
        if admission["policy_hash"] != current_policy_hash:
            raise ValidationError("ATTEMPT-POLICY-HASH-MISMATCH")
        _validate_run_binding(admission, run_binding)
        if state["state"] != "admitted":
            raise ValidationError("ATTEMPT-NOT-ADMITTED")
        return _append_attempt_locked(
            control,
            entries,
            events,
            admission_id,
            "started",
            run_binding,
            _default_payload("attempt_claimed"),
        )


def request_stop(
    workspace: Path, admission_id: str, reason_code: str, evidence: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """Append a stop request; it never claims to kill a remote process."""

    _, current_policy_hash = require_agent_execution_allowed("attempt-stop")
    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
        events = _read_attempts_locked(control, entries)
        states = _attempt_states(entries, events)
        state = states.get(admission_id)
        if state is None:
            raise ValidationError("ATTEMPT-ADMISSION-NOT-FOUND")
        admission = state["entry"]
        if admission["policy_hash"] != current_policy_hash:
            raise ValidationError("ATTEMPT-POLICY-HASH-MISMATCH")
        binding = {
            "run_id": None,
            "run_hash": None,
            "wave_id": admission["wave_id"],
            "role_id": admission["role_id"],
        }
        payload = _default_payload(reason_code)
        payload["evidence"] = evidence or []
        if state["state"] == "admitted":
            payload.update({"outcome": "stopped", "retest_required": True})
            return _append_attempt_locked(
                control, entries, events, admission_id, "terminal", binding, payload
            )
        if state["state"] != "in_flight":
            raise ValidationError("ATTEMPT-NOT-STOPPABLE")
        return _append_attempt_locked(
            control, entries, events, admission_id, "stop_requested", binding, payload
        )


def recover_attempt(
    workspace: Path,
    admission_id: str,
    *,
    started_event_hash: str | None,
    reason_code: str,
    evidence: list[dict[str, str]],
    outcome: str = "failed",
) -> dict[str, Any]:
    """Terminalize one stranded permit without enabling any new execution.

    This is deliberately outside ``require_agent_execution_allowed``: a freeze
    must block *new* work, not make a previously claimed permit impossible to
    audit and close.  It only appends a terminal ``failed``/``stopped`` event,
    requires immutable evidence, and reuses the original admission policy and
    started run binding.  It cannot start, resume, or claim an attempt.
    """

    if outcome not in {"failed", "stopped"}:
        raise ValidationError("ATTEMPT-RECOVERY-OUTCOME-INVALID")
    if not isinstance(reason_code, str) or not reason_code.strip():
        raise ValidationError("ATTEMPT-RECOVERY-REASON-INVALID")
    if not _is_nonempty_artifact_refs(evidence):
        raise ValidationError("ATTEMPT-RECOVERY-EVIDENCE-REQUIRED")
    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
        events = _read_attempts_locked(control, entries)
        states = _attempt_states(entries, events)
        state = states.get(admission_id)
        if state is None:
            raise ValidationError("ATTEMPT-ADMISSION-NOT-FOUND")
        if state["state"] not in {"admitted", "in_flight", "stop_requested"}:
            raise ValidationError("ATTEMPT-NOT-RECOVERABLE")
        admission = state["entry"]
        started = next(
            (
                event
                for event in reversed(events)
                if event["admission_id"] == admission_id
                and event["event_type"] == "started"
            ),
            None,
        )
        if started is None:
            if started_event_hash is not None or state["state"] != "admitted":
                raise ValidationError("ATTEMPT-RECOVERY-STARTED-BINDING-INVALID")
            binding = {
                "run_id": None,
                "run_hash": None,
                "wave_id": admission["wave_id"],
                "role_id": admission["role_id"],
            }
        else:
            if started_event_hash != started["event_hash"]:
                raise ValidationError("ATTEMPT-RECOVERY-STARTED-BINDING-INVALID")
            binding = started["run_binding"]
        payload = {
            "outcome": "stopped" if state["state"] == "stop_requested" else outcome,
            "reason_code": reason_code,
            "execution_record": None,
            "evidence": evidence,
            "retest_required": True,
        }
        return _append_attempt_locked(
            control,
            entries,
            events,
            admission_id,
            "terminal",
            binding,
            payload,
        )


def settle_attempt(
    workspace: Path,
    admission_id: str,
    outcome: str,
    reason_code: str,
    execution_record: dict[str, str] | None,
    evidence: list[dict[str, str]] | None = None,
    retest_required: bool = False,
) -> dict[str, Any]:
    """Append one immutable terminal receipt for a claimed work permit."""

    _, current_policy_hash = require_agent_execution_allowed("attempt-settlement")
    if outcome not in {"completed", "failed", "stopped"}:
        raise ValidationError("ATTEMPT-OUTCOME-INVALID")
    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
        events = _read_attempts_locked(control, entries)
        states = _attempt_states(entries, events)
        state = states.get(admission_id)
        if state is None:
            raise ValidationError("ATTEMPT-ADMISSION-NOT-FOUND")
        admission = state["entry"]
        if admission["policy_hash"] != current_policy_hash:
            raise ValidationError("ATTEMPT-POLICY-HASH-MISMATCH")
        if state["state"] not in {"in_flight", "stop_requested"}:
            raise ValidationError("ATTEMPT-NOT-SETTLABLE")
        started = next(
            event
            for event in reversed(events)
            if event["admission_id"] == admission_id and event["event_type"] == "started"
        )
        if state["state"] == "stop_requested":
            outcome = "stopped"
            retest_required = True
        payload = {
            "outcome": outcome,
            "reason_code": reason_code,
            "execution_record": execution_record,
            "evidence": evidence or [],
            "retest_required": retest_required,
        }
        return _append_attempt_locked(
            control,
            entries,
            events,
            admission_id,
            "terminal",
            started["run_binding"],
            payload,
        )


def attempt_status(workspace: Path, admission_id: str) -> str:
    """Read a replayed permit state without creating any ledger files."""

    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
        events = _read_attempts_locked(control, entries)
        state = _attempt_states(entries, events).get(admission_id)
    if state is None:
        raise ValidationError("ATTEMPT-ADMISSION-NOT-FOUND")
    return str(state["state"])


def load_terminal_attempt(workspace: Path, admission_id: str) -> dict[str, Any]:
    """Load the immutable receipt later required by a rework lineage.

    It never decides whether an attempt repaired a blocker; it only returns a
    verified ledger binding after replaying the complete hash chain.
    """

    with _locked_ledger(workspace) as control:
        entries = _read_entries_locked(control)
        events = _read_attempts_locked(control, entries)
        states = _attempt_states(entries, events)
        state = states.get(admission_id)
    if state is None:
        raise ValidationError("ATTEMPT-ADMISSION-NOT-FOUND")
    if state["state"] not in {"completed", "failed", "stopped"}:
        raise ValidationError("ATTEMPT-NOT-TERMINAL")
    terminal = state["event"]
    if terminal is None or terminal.get("event_type") != "terminal":
        raise ValidationError("ATTEMPT-TERMINAL-RECEIPT-MISSING")
    return {
        "admission": state["entry"],
        "terminal_event": terminal,
        "state": state["state"],
    }


def inspect_nonterminal_attempts(workspace: Path) -> list[dict[str, Any]]:
    """Return stranded admission metadata without creating or changing files."""

    root = workspace.resolve()
    control = root / "control"
    if not control.exists():
        return []
    admissions = control / "admissions"
    attempts_dir = control / "attempts"
    lock_path = control / "ledger.lock"
    if (
        control.is_symlink()
        or not control.is_dir()
        or admissions.is_symlink()
        or not admissions.is_dir()
        or attempts_dir.is_symlink()
        or not attempts_dir.is_dir()
        or lock_path.is_symlink()
        or not lock_path.is_file()
    ):
        raise ValidationError("CONTROL-LEDGER-READ-INVALID")
    try:
        fd = os.open(lock_path, os.O_RDONLY)
    except OSError as exc:
        raise ValidationError("CONTROL-LEDGER-READ-INVALID") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        entries = _read_entries_locked(control)
        events = _read_attempts_locked(control, entries)
        states = _attempt_states(entries, events)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    results: list[dict[str, Any]] = []
    for admission_id, state in states.items():
        if state["state"] not in {"admitted", "in_flight", "stop_requested"}:
            continue
        started = next(
            (
                event
                for event in reversed(events)
                if event["admission_id"] == admission_id
                and event["event_type"] == "started"
            ),
            None,
        )
        results.append(
            {
                "admission_id": admission_id,
                "state": state["state"],
                "policy_hash": state["entry"]["policy_hash"],
                "wave_id": state["entry"]["wave_id"],
                "role_id": state["entry"]["role_id"],
                "started_event_hash": started["event_hash"] if started else None,
                "run_binding": started["run_binding"] if started else None,
                "recommended_action": "recover_attempt_with_evidence",
            }
        )
    return sorted(results, key=lambda item: item["admission_id"])
