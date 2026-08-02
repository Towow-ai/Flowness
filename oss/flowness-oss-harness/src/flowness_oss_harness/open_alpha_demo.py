"""Runnable Open Alpha demonstration of Flowness orchestration semantics.

The default runner is deliberately deterministic and costs no model tokens. It
does not measure model quality. It proves the harness shape: parallel isolated
producers, schema-bound outputs, candidate/policy-bound independent juries,
fail-closed aggregation, targeted rework, successor evidence and fresh retest.

``runner=codex`` replaces only the three producer fixtures with real Codex CLI
processes. The deterministic judges remain policy probes so the demonstration
always exercises the same credible FAIL and recovery path.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .integrity import canonical_hash, verify_self_hash
from .models import utc_now
from .registry import ValidationError, atomic_write_json
from .resources import SCHEMAS_ROOT
from .schema_validation import (
    load_validated_json,
    validate_openai_response_format_schema,
    validate_payload,
)


PRODUCER_SCHEMA = SCHEMAS_ROOT / "open-alpha-demo-producer-result.schema.json"
JURY_SCHEMA = SCHEMAS_ROOT / "open-alpha-demo-jury-report.schema.json"
TRACE_SCHEMA = SCHEMAS_ROOT / "open-alpha-demo-trace.schema.json"
PRODUCER_ROLES = (
    "producer.product",
    "producer.architecture",
    "producer.quickstart",
)
JUDGE_ROLES = ("judge.truth", "judge.structure")
BLOCKER_ID = "BLK-DEMO-TRUTH-001"


@dataclass(frozen=True)
class _ProducerSpec:
    role_id: str
    title: str
    mission: str
    summary: str
    claim_id: str
    claim_text: str
    maturity: str
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    deliverables: tuple[str, ...]


_PRODUCERS = (
    _ProducerSpec(
        "producer.product",
        "Product position",
        "Explain who Flowness is for and state one evidence-bounded product claim.",
        "Flowness turns a goal into independently judged, traceable agent work.",
        "claim-product-position",
        # This is intentionally unsupported. The truth judge must block it.
        "Flowness is production-verified for every multi-agent engineering workflow.",
        "current_verified",
        ("fixture:product-draft",),
        (),
        ("one-line-positioning", "audience-boundary"),
    ),
    _ProducerSpec(
        "producer.architecture",
        "Architecture slice",
        "Describe the control, execution and evidence path without claiming hosted runtime proof.",
        "Three isolated producers feed one sealed candidate; juries cannot average away a failure.",
        "claim-architecture-slice",
        "The Open Alpha demo implements isolated producer outputs and fail-closed jury aggregation.",
        "experimental",
        ("fixture:architecture-output",),
        ("This fixture proves orchestration semantics, not production reliability.",),
        ("control-plane", "evidence-plane", "failure-path"),
    ),
    _ProducerSpec(
        "producer.quickstart",
        "Ten-minute path",
        "Give a local run and inspect path that requires no model account.",
        "One command creates the trace; a second command independently verifies every binding.",
        "claim-local-quickstart",
        "The deterministic fixture can exercise FAIL, rework and PASS offline.",
        "experimental",
        ("fixture:quickstart-output",),
        ("The fixture does not score model reasoning or external adoption.",),
        ("run-command", "inspect-command"),
    ),
)


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"demo artifact must be a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _relative_ref(root: Path, path: Path) -> dict[str, str]:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved_root not in resolved.parents:
        raise ValidationError(f"demo artifact escapes run root: {path}")
    return {"path": resolved.relative_to(resolved_root).as_posix(), "sha256": _sha256_file(resolved)}


def _resolve_ref(root: Path, ref: dict[str, Any], label: str) -> Path:
    value = ref.get("path")
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValidationError(f"{label} has unsafe path")
    unresolved = root / value
    if unresolved.is_symlink():
        raise ValidationError(f"{label} cannot be a symlink")
    path = unresolved.resolve()
    if root.resolve() not in path.parents or _sha256_file(path) != ref.get("sha256"):
        raise ValidationError(f"{label} hash or boundary mismatch")
    return path


class _Events:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []

    def add(self, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._rows.append(
                {
                    "event_type": event_type,
                    "actor": actor,
                    "at": utc_now(),
                    "payload": payload,
                }
            )

    def seal(self, path: Path) -> None:
        previous: str | None = None
        sealed: list[dict[str, Any]] = []
        for sequence, row in enumerate(self._rows, 1):
            unsigned = {"sequence": sequence, "previous_event_hash": previous, **row}
            event = {**unsigned, "event_hash": canonical_hash(unsigned)}
            previous = event["event_hash"]
            sealed.append(event)
        _atomic_write_text(
            path,
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in sealed),
        )


def _fixture_result(spec: _ProducerSpec) -> dict[str, Any]:
    return {
        "schema_version": "open-alpha-demo-producer-result/v1",
        "role_id": spec.role_id,
        "agent_instance_id": "agent-" + spec.role_id.replace(".", "-"),
        "source_mode": "fixture",
        "result": {
            "title": spec.title,
            "summary": spec.summary,
            "claims": [
                {
                    "claim_id": spec.claim_id,
                    "text": spec.claim_text,
                    "maturity": spec.maturity,
                    "evidence_ids": list(spec.evidence_ids),
                    "limitations": list(spec.limitations),
                }
            ],
            "deliverables": list(spec.deliverables),
        },
    }


def _codex_result(
    spec: _ProducerSpec,
    output_root: Path,
    codex_bin: str,
) -> dict[str, Any]:
    output_path = output_root / "result.json"
    execution_path = output_root / "execution.json"
    validate_openai_response_format_schema(PRODUCER_SCHEMA, "Open Alpha producer")
    prompt = f"""You are {spec.role_id}, one producer in a Flowness Open Alpha demo.
Mission: {spec.mission}
Return only JSON matching the supplied schema. Bind role_id to {spec.role_id},
agent_instance_id to agent-{spec.role_id.replace('.', '-')}, and source_mode to codex.
Produce at least one claim. Use experimental or designed_target unless you have two
independent evidence groups including code, test, event, or runtime evidence.
Do not use claim_id claim-product-position; the controller owns that negative probe.
This run is an orchestration demonstration; it is not production verification.
"""
    command = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--cd",
        str(output_root),
        "--output-schema",
        str(PRODUCER_SCHEMA),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    atomic_write_json(
        execution_path,
        {
            "runner": "codex",
            "returncode": completed.returncode,
            "stdout_sha256": "sha256:" + hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": "sha256:" + hashlib.sha256(completed.stderr.encode()).hexdigest(),
        },
    )
    if completed.returncode != 0:
        raise ValidationError(f"Codex producer failed: {spec.role_id} (exit {completed.returncode})")
    result = load_validated_json(output_path, PRODUCER_SCHEMA, spec.role_id)
    if (
        result.get("role_id") != spec.role_id
        or result.get("agent_instance_id") != "agent-" + spec.role_id.replace(".", "-")
        or result.get("source_mode") != "codex"
    ):
        raise ValidationError(f"Codex producer identity mismatch: {spec.role_id}")
    for claim in result["result"]["claims"]:
        if claim["claim_id"] == "claim-product-position":
            raise ValidationError("Codex producer cannot replace the controller's negative probe")
        if claim["maturity"] == "current_verified":
            raise ValidationError("Codex demo producers cannot self-promote a verified claim")
        if claim["maturity"] == "experimental" and not claim["limitations"]:
            raise ValidationError("Codex experimental claim must preserve a limitation")
    return result


def _run_producers(
    root: Path,
    runner: str,
    codex_bin: str,
    events: _Events,
) -> list[Path]:
    barrier = threading.Barrier(len(_PRODUCERS))

    def execute(spec: _ProducerSpec) -> Path:
        output_root = root / "agents" / spec.role_id
        output_root.mkdir(parents=True, exist_ok=False)
        events.add("producer_started", spec.role_id, {"output_root": output_root.relative_to(root).as_posix()})
        barrier.wait(timeout=30)
        if runner == "fixture":
            result = _fixture_result(spec)
            output_path = output_root / "result.json"
            validate_payload(result, PRODUCER_SCHEMA, spec.role_id)
            atomic_write_json(output_path, result)
        else:
            result = _codex_result(spec, output_root, codex_bin)
            output_path = output_root / "result.json"
        events.add(
            "producer_completed",
            spec.role_id,
            {"output": _relative_ref(root, output_path), "schema": PRODUCER_SCHEMA.name},
        )
        return output_path

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="flowness-producer") as pool:
        paths = list(pool.map(execute, _PRODUCERS))
    return paths


def _candidate_payload(
    root: Path,
    producer_paths: list[Path],
    *,
    predecessor: dict[str, Any] | None = None,
    successor_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    sources: list[dict[str, str]] = []
    claims: list[dict[str, Any]] = []
    for path in producer_paths:
        payload = load_validated_json(path, PRODUCER_SCHEMA, "producer result")
        sources.append({"role_id": payload["role_id"], **_relative_ref(root, path)})
        claims.extend(payload["result"]["claims"])
    claim_ids = [claim["claim_id"] for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValidationError("Open Alpha demo producer claim ids must be unique")
    if "claim-product-position" not in claim_ids:
        # Real-agent mode keeps model outputs evidence-bounded. The controller
        # adds this explicitly labelled negative probe so the teaching path is
        # stable and never depends on a model volunteering a false claim.
        claims.append(
            {
                "claim_id": "claim-product-position",
                "text": "Flowness is production-verified for every multi-agent engineering workflow.",
                "maturity": "current_verified",
                "evidence_ids": ["fixture:controller-negative-probe"],
                "limitations": [],
            }
        )
    if predecessor is not None:
        product = next(claim for claim in claims if claim["claim_id"] == "claim-product-position")
        product["maturity"] = "experimental"
        product["limitations"] = [
            "The Open Alpha demo validates orchestration semantics only; production reliability remains unverified."
        ]
    unsigned: dict[str, Any] = {
        "schema_version": "open-alpha-demo-candidate/v1",
        "sources": sources,
        "claims": claims,
        "lineage": (
            {"kind": "initial"}
            if predecessor is None
            else {
                "kind": "successor",
                "predecessor_candidate_id": predecessor["candidate_id"],
                "predecessor_candidate_hash": predecessor["candidate_hash"],
                "blocker_id": BLOCKER_ID,
                "successor_evidence": successor_evidence,
            }
        ),
    }
    candidate_id = "candidate-" + canonical_hash(unsigned).removeprefix("sha256:")[:20]
    return {**unsigned, "candidate_id": candidate_id, "candidate_hash": canonical_hash({**unsigned, "candidate_id": candidate_id})}


def _verify_candidate(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "open-alpha-demo-candidate/v1":
        raise ValidationError("unsupported Open Alpha demo candidate")
    expected = canonical_hash({key: value for key, value in payload.items() if key != "candidate_hash"})
    if payload.get("candidate_hash") != expected:
        raise ValidationError("Open Alpha demo candidate hash mismatch")
    expected_id = "candidate-" + canonical_hash(
        {key: value for key, value in payload.items() if key not in {"candidate_id", "candidate_hash"}}
    ).removeprefix("sha256:")[:20]
    if payload.get("candidate_id") != expected_id:
        raise ValidationError("Open Alpha demo candidate id mismatch")


def _judge_report(
    role_id: str,
    round_number: int,
    candidate: dict[str, Any],
    candidate_ref: dict[str, str],
    policy: dict[str, Any],
    policy_ref: dict[str, str],
) -> dict[str, Any]:
    if role_id == "judge.truth":
        unsupported = [
            claim
            for claim in candidate["claims"]
            if claim["maturity"] == "current_verified"
            and (
                len(claim["evidence_ids"]) < 2
                or not any(
                    evidence.startswith(("runtime:", "test:", "event:"))
                    for evidence in claim["evidence_ids"]
                )
            )
        ]
        missing_limits = [
            claim
            for claim in candidate["claims"]
            if claim["maturity"] == "experimental" and not claim["limitations"]
        ]
        passed = not unsupported and not missing_limits
        observed = (
            "All claims carry policy-compatible maturity and limitations."
            if passed
            else "Unsupported current_verified claim(s): "
            + ", ".join(claim["claim_id"] for claim in unsupported + missing_limits)
        )
        check_id = "G3.truth-claims"
        expected = "Verified claims need two evidence groups plus runtime/test/event evidence; experimental claims name limits."
        retest = "Replace only the unsupported maturity assertion, add its limitation, seal successor evidence, then run fresh juries."
    else:
        roles = {item["role_id"] for item in candidate["sources"]}
        isolated = all(item["path"] == f"agents/{item['role_id']}/result.json" for item in candidate["sources"])
        passed = roles == set(PRODUCER_ROLES) and isolated
        observed = (
            "Three required producer roles are hash-bound to distinct output roots."
            if passed
            else "Producer set or isolated output binding is incomplete."
        )
        check_id = "G3.harness-structure"
        expected = "Exactly three distinct producer roles, each with one isolated schema-valid output."
        retest = "Re-run missing producer(s) in distinct roots and reseal the candidate."
    unsigned = {
        "schema_version": "open-alpha-demo-jury-report/v1",
        "report_id": f"report-r{round_number}-{role_id.replace('.', '-')}",
        "round": round_number,
        "judge": {
            "role_id": role_id,
            "agent_instance_id": f"agent-r{round_number}-{role_id.replace('.', '-')}",
            "independent": True,
            "candidate_author": False,
        },
        "candidate_id": candidate["candidate_id"],
        "candidate_sha256": candidate_ref["sha256"],
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_ref["sha256"],
        "verdict": "pass" if passed else "fail",
        "check": {
            "check_id": check_id,
            "expected": expected,
            "observed": observed,
            "retest_condition": retest,
        },
        "blocker_id": None if passed else (BLOCKER_ID if role_id == "judge.truth" else "BLK-DEMO-STRUCTURE-001"),
        "evidence_refs": [candidate_ref["sha256"], policy_ref["sha256"]],
    }
    return {**unsigned, "report_hash": canonical_hash(unsigned)}


def _run_jury(
    root: Path,
    round_number: int,
    candidate_path: Path,
    policy_path: Path,
    events: _Events,
) -> tuple[list[Path], str, list[str]]:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    _verify_candidate(candidate)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    candidate_ref = _relative_ref(root, candidate_path)
    policy_ref = _relative_ref(root, policy_path)
    barrier = threading.Barrier(2)

    def execute(role_id: str) -> Path:
        output_root = root / "jury" / f"round-{round_number}" / role_id
        output_root.mkdir(parents=True, exist_ok=False)
        events.add("judge_started", role_id, {"round": round_number, "candidate_id": candidate["candidate_id"]})
        barrier.wait(timeout=30)
        report = _judge_report(role_id, round_number, candidate, candidate_ref, policy, policy_ref)
        validate_payload(report, JURY_SCHEMA, role_id)
        output = output_root / "report.json"
        atomic_write_json(output, report)
        events.add("judge_completed", role_id, {"round": round_number, "report": _relative_ref(root, output), "verdict": report["verdict"]})
        return output

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"flowness-jury-r{round_number}") as pool:
        paths = list(pool.map(execute, JUDGE_ROLES))
    reports = [load_validated_json(path, JURY_SCHEMA, "jury report") for path in paths]
    blocking = [report["report_id"] for report in reports if report["verdict"] in {"fail", "unknown"}]
    decision = "blocked" if blocking else "accepted"
    events.add("jury_decided", "controller", {"round": round_number, "decision": decision, "blocking_report_ids": blocking, "aggregation": "any_fail_or_unknown_blocks_no_average"})
    return paths, decision, blocking


def run_open_alpha_demo(
    output: Path,
    *,
    runner: str = "fixture",
    codex_bin: str = "codex",
) -> dict[str, Any]:
    """Execute and seal a complete two-round Open Alpha harness demonstration."""

    if runner not in {"fixture", "codex"}:
        raise ValidationError("Open Alpha demo runner must be fixture or codex")
    output = output.resolve()
    if output.exists() or output.is_symlink():
        raise ValidationError("Open Alpha demo output already exists; choose a new directory")
    output.mkdir(parents=True)
    events = _Events()
    events.add("run_started", "controller", {"runner": runner, "producer_count": 3, "judge_count": 2})

    policy = {
        "schema_version": "open-alpha-demo-policy/v1",
        "policy_id": "open-alpha-demo-policy/v1",
        "verified_claim_rule": "two evidence groups and at least one runtime, test, or event evidence id",
        "experimental_claim_rule": "at least one explicit limitation",
        "jury_rule": "any credible fail or unknown blocks; scores and averages are forbidden",
        "minimum_independent_judges": 2,
    }
    policy_path = output / "policy.json"
    atomic_write_json(policy_path, policy)
    policy_ref = _relative_ref(output, policy_path)

    producer_paths = _run_producers(output, runner, codex_bin, events)
    candidate_a = _candidate_payload(output, producer_paths)
    candidate_a_path = output / "candidates" / "round-1.json"
    atomic_write_json(candidate_a_path, candidate_a)
    events.add("candidate_sealed", "controller", {"round": 1, "candidate_id": candidate_a["candidate_id"], "candidate": _relative_ref(output, candidate_a_path)})
    jury_a, decision_a, blocking_a = _run_jury(output, 1, candidate_a_path, policy_path, events)
    if decision_a != "blocked" or "report-r1-judge-truth" not in blocking_a:
        raise ValidationError("demo invariant failed: round one must credibly block")

    rework_dir = output / "rework"
    rework_dir.mkdir()
    rework_manifest = {
        "schema_version": "open-alpha-demo-rework/v1",
        "blocker_id": BLOCKER_ID,
        "origin_candidate_id": candidate_a["candidate_id"],
        "origin_candidate_hash": candidate_a["candidate_hash"],
        "target_claim_id": "claim-product-position",
        "targeted_fields": [
            "claims.claim-product-position.maturity",
            "claims.claim-product-position.limitations",
        ],
        "unchanged_claim_ids": ["claim-architecture-slice", "claim-local-quickstart"],
        "retest_condition": "fresh independent truth and structure reports bind the successor candidate and same policy",
    }
    rework_manifest_path = rework_dir / "manifest.json"
    atomic_write_json(rework_manifest_path, rework_manifest)
    rework_evidence = {
        "schema_version": "open-alpha-demo-successor-evidence/v1",
        "blocker_id": BLOCKER_ID,
        "origin_candidate_id": candidate_a["candidate_id"],
        "change": {
            "claim_id": "claim-product-position",
            "maturity_before": "current_verified",
            "maturity_after": "experimental",
            "limitation_added": "The Open Alpha demo validates orchestration semantics only; production reliability remains unverified.",
        },
        "producer_artifacts_reused_without_mutation": [_relative_ref(output, path) for path in producer_paths],
    }
    rework_evidence_path = rework_dir / "successor-evidence.json"
    atomic_write_json(rework_evidence_path, rework_evidence)
    rework_evidence_ref = _relative_ref(output, rework_evidence_path)
    events.add("targeted_rework_completed", "rework.agent", {"blocker_id": BLOCKER_ID, "manifest": _relative_ref(output, rework_manifest_path), "successor_evidence": rework_evidence_ref})

    candidate_b = _candidate_payload(
        output,
        producer_paths,
        predecessor=candidate_a,
        successor_evidence=rework_evidence_ref,
    )
    candidate_b_path = output / "candidates" / "round-2.json"
    atomic_write_json(candidate_b_path, candidate_b)
    events.add("candidate_sealed", "controller", {"round": 2, "candidate_id": candidate_b["candidate_id"], "candidate": _relative_ref(output, candidate_b_path)})
    jury_b, decision_b, blocking_b = _run_jury(output, 2, candidate_b_path, policy_path, events)
    if decision_b != "accepted" or blocking_b:
        raise ValidationError("demo invariant failed: successor must pass fresh juries")
    events.add("run_completed", "controller", {"state": "accepted_after_targeted_rework", "successor_candidate_id": candidate_b["candidate_id"]})

    event_path = output / "events.jsonl"
    events.seal(event_path)
    unsigned_trace = {
        "schema_version": "open-alpha-demo-trace/v1",
        "run_id": "demo-open-alpha-001",
        "runner": runner,
        "semantics_boundary": (
            "The fixture runner validates orchestration semantics, bindings and failure recovery; "
            "it does not measure model quality, production reliability or external adoption."
        ),
        "policy": policy_ref,
        "producer_wave": {
            "max_parallel": 3,
            "barrier_parties": 3,
            "roles": list(PRODUCER_ROLES),
            "outputs": [_relative_ref(output, path) for path in producer_paths],
        },
        "rounds": [
            {
                "round": 1,
                "candidate": _relative_ref(output, candidate_a_path),
                "reports": [_relative_ref(output, path) for path in jury_a],
                "decision": decision_a,
                "blocking_report_ids": blocking_a,
            },
            {
                "round": 2,
                "candidate": _relative_ref(output, candidate_b_path),
                "reports": [_relative_ref(output, path) for path in jury_b],
                "decision": decision_b,
                "blocking_report_ids": blocking_b,
            },
        ],
        "rework": {
            "blocker_id": BLOCKER_ID,
            "targeted_fields": rework_manifest["targeted_fields"],
            "manifest": _relative_ref(output, rework_manifest_path),
            "successor_evidence": rework_evidence_ref,
        },
        "event_log": _relative_ref(output, event_path),
    }
    trace = {**unsigned_trace, "trace_hash": canonical_hash(unsigned_trace)}
    validate_payload(trace, TRACE_SCHEMA, "Open Alpha demo trace")
    trace_path = output / "trace.json"
    atomic_write_json(trace_path, trace)
    return inspect_open_alpha_demo(output)


def _read_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"demo event log invalid at line {line_number}") from exc
        rows.append(row)
    previous: str | None = None
    for sequence, row in enumerate(rows, 1):
        if row.get("sequence") != sequence or row.get("previous_event_hash") != previous:
            raise ValidationError("demo event log sequence or predecessor mismatch")
        verify_self_hash(row, "event_hash")
        previous = row["event_hash"]
    return rows


def inspect_open_alpha_demo(root: Path) -> dict[str, Any]:
    """Verify the complete artifact, jury, rework and event lineage read-only."""

    root = root.resolve()
    trace_path = root / "trace.json"
    trace = load_validated_json(trace_path, TRACE_SCHEMA, "Open Alpha demo trace")
    verify_self_hash(trace, "trace_hash")
    policy_path = _resolve_ref(root, trace["policy"], "policy")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if (
        policy.get("policy_id") != "open-alpha-demo-policy/v1"
        or policy.get("minimum_independent_judges") != 2
        or "any credible fail or unknown blocks" not in policy.get("jury_rule", "")
    ):
        raise ValidationError("demo policy does not preserve fail-closed jury semantics")

    producer_payloads: list[dict[str, Any]] = []
    for ref in trace["producer_wave"]["outputs"]:
        path = _resolve_ref(root, ref, "producer output")
        payload = load_validated_json(path, PRODUCER_SCHEMA, "producer output")
        if path.relative_to(root).as_posix() != f"agents/{payload['role_id']}/result.json":
            raise ValidationError("producer output isolation mismatch")
        producer_payloads.append(payload)
    if {payload["role_id"] for payload in producer_payloads} != set(PRODUCER_ROLES):
        raise ValidationError("demo producer role coverage mismatch")

    candidates: list[dict[str, Any]] = []
    reports_by_round: list[list[dict[str, Any]]] = []
    for expected_round, round_ref in enumerate(trace["rounds"], 1):
        if round_ref["round"] != expected_round:
            raise ValidationError("demo jury round order mismatch")
        candidate_path = _resolve_ref(root, round_ref["candidate"], "candidate")
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        _verify_candidate(candidate)
        candidates.append(candidate)
        reports: list[dict[str, Any]] = []
        for report_ref in round_ref["reports"]:
            report_path = _resolve_ref(root, report_ref, "jury report")
            report = load_validated_json(report_path, JURY_SCHEMA, "jury report")
            verify_self_hash(report, "report_hash")
            if (
                report["round"] != expected_round
                or report["candidate_id"] != candidate["candidate_id"]
                or report["candidate_sha256"] != round_ref["candidate"]["sha256"]
                or report["policy_sha256"] != trace["policy"]["sha256"]
            ):
                raise ValidationError("jury report candidate or policy binding mismatch")
            reports.append(report)
        if {report["judge"]["role_id"] for report in reports} != set(JUDGE_ROLES):
            raise ValidationError("two independent jury dimensions are required")
        if len({report["judge"]["agent_instance_id"] for report in reports}) != 2:
            raise ValidationError("jury agent identities must be independent")
        blocking = [report["report_id"] for report in reports if report["verdict"] in {"fail", "unknown"}]
        expected_decision = "blocked" if blocking else "accepted"
        if round_ref["decision"] != expected_decision or round_ref["blocking_report_ids"] != blocking:
            raise ValidationError("jury decision attempted to average or hide a blocker")
        reports_by_round.append(reports)

    if (
        trace["rounds"][0]["decision"] != "blocked"
        or {
            report["blocker_id"]
            for report in reports_by_round[0]
            if report["verdict"] in {"fail", "unknown"}
        }
        != {BLOCKER_ID}
        or trace["rounds"][1]["decision"] != "accepted"
        or any(report["verdict"] != "pass" for report in reports_by_round[1])
    ):
        raise ValidationError("demo must preserve FAIL then successor PASS lifecycle")
    round_1_agents = {
        report["judge"]["agent_instance_id"] for report in reports_by_round[0]
    }
    round_2_agents = {
        report["judge"]["agent_instance_id"] for report in reports_by_round[1]
    }
    if round_1_agents & round_2_agents:
        raise ValidationError("successor jury must use fresh agent identities")

    manifest_path = _resolve_ref(root, trace["rework"]["manifest"], "rework manifest")
    evidence_path = _resolve_ref(root, trace["rework"]["successor_evidence"], "successor evidence")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    origin, successor = candidates
    if (
        origin["candidate_id"] == successor["candidate_id"]
        or trace["rework"].get("blocker_id") != BLOCKER_ID
        or manifest.get("blocker_id") != BLOCKER_ID
        or evidence.get("blocker_id") != BLOCKER_ID
        or successor["lineage"].get("predecessor_candidate_id") != origin["candidate_id"]
        or successor["lineage"].get("blocker_id") != BLOCKER_ID
        or successor["lineage"].get("successor_evidence") != trace["rework"]["successor_evidence"]
        or manifest.get("origin_candidate_id") != origin["candidate_id"]
        or evidence.get("origin_candidate_id") != origin["candidate_id"]
    ):
        raise ValidationError("successor lineage or new evidence binding mismatch")
    before = {claim["claim_id"]: claim for claim in origin["claims"]}
    after = {claim["claim_id"]: claim for claim in successor["claims"]}
    if set(before) != set(after):
        raise ValidationError("targeted rework changed candidate claim membership")
    changed = {
        claim_id
        for claim_id in before
        if before[claim_id] != after[claim_id]
    }
    if changed != {"claim-product-position"}:
        raise ValidationError("targeted rework changed unrelated claims")
    if (
        after["claim-product-position"]["maturity"] != "experimental"
        or not after["claim-product-position"]["limitations"]
    ):
        raise ValidationError("targeted rework did not satisfy truth retest condition")

    events = _read_events(_resolve_ref(root, trace["event_log"], "event log"))
    started_positions = [index for index, row in enumerate(events) if row["event_type"] == "producer_started"]
    completed_positions = [index for index, row in enumerate(events) if row["event_type"] == "producer_completed"]
    if len(started_positions) != 3 or len(completed_positions) != 3 or max(started_positions) > min(completed_positions):
        raise ValidationError("producer wave does not prove three concurrently admitted workers")
    return {
        "state": "verified",
        "run_id": trace["run_id"],
        "runner": trace["runner"],
        "producer_agents": 3,
        "judge_agents_per_round": 2,
        "round_1": "blocked",
        "blocker_id": BLOCKER_ID,
        "targeted_rework": "verified",
        "successor_evidence": trace["rework"]["successor_evidence"],
        "round_2": "accepted",
        "event_count": len(events),
        "trace": str(trace_path),
        "boundary": trace["semantics_boundary"],
    }
