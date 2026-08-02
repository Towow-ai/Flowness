from __future__ import annotations

"""Derive an evidence-bounded explanation pack from a verified local demo.

The pack is intentionally an *interpreter* of ``demo-run.json`` rather than a
second hand-written story.  It copies the input ledger only into a disposable
derivation directory to exercise projection and review APIs; no source demo
artifact is mutated.  Its stable output records only facts that are
deterministic for the verified input, never the replay's random record IDs or
timestamps.
"""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .demo import verify_change_evidence_demo
from .ledger import Ledger, LedgerError
from .projection import read_fresh_type_projection, rebuild_type_projection
from .review import build_review_verdict


SCHEMA = "flowness-ledger-core-demo-scenario-pack/v1"
BOUNDARY = "public_open_alpha_local_explanation_not_production"
PACK_FILE = "scenario-pack.json"
TIMELINE_FILE = "timeline.json"
MARKDOWN_FILE = "timeline.md"
MERMAID_FILE = "timeline.mmd"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(_canonical(value) + b"\n")


def _new_empty_directory(path: Path | str) -> Path:
    destination = Path(path)
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir() or any(destination.iterdir()):
            raise LedgerError("scenario pack directory must be a new empty regular directory")
    else:
        destination.mkdir(parents=True)
    return destination.resolve(strict=True)


def _regular_child(root: Path, name: str) -> Path:
    candidate = (root / name).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LedgerError("scenario pack artifact escapes its directory") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise LedgerError("scenario pack artifact is unsafe")
    return candidate


def _event_rows(ledger: Ledger) -> list[dict[str, Any]]:
    """Return a small audience-safe projection of local audit records."""

    rows: list[dict[str, Any]] = []
    for row in ledger.read("audit"):
        payload_type = row["payload"].get("type")
        rows.append(
            {
                "sequence": row["sequence"],
                "kind": row["kind"],
                "proposal_id": row["proposal_id"],
                "outcome": row["outcome"],
                "payload_type": payload_type if isinstance(payload_type, str) else None,
            }
        )
    return rows


def _derive_verified_facts(demo_root: Path) -> dict[str, Any]:
    """Recompute all pack facts from the input demo, without mutating it."""

    demo = verify_change_evidence_demo(demo_root)
    root = demo_root.resolve(strict=True)
    ledger_relative = demo["artifacts"]["ledger"]["path"]
    recovery_relative = demo["artifacts"]["recovery_report"]["path"]
    if ledger_relative != "ledger/ledger.jsonl" or not isinstance(recovery_relative, str):
        raise LedgerError("demo artifact layout is not eligible for scenario derivation")
    source_ledger_path = _regular_child(root, ledger_relative)
    source_recovery_path = _regular_child(root, recovery_relative)
    source_ledger = Ledger.open(source_ledger_path.parent)

    accepted = build_review_verdict(source_ledger, "P-accepted")
    rejected = build_review_verdict(source_ledger, "P-rejected")
    if accepted["verdict"] != "accepted_committed" or rejected["verdict"] != "rejected_not_committed":
        raise LedgerError("demo does not retain the expected terminal review paths")

    # Projection stale-read and pending-verdict refusal need a new pending
    # proposal.  Exercise them on a byte-copy so a scenario never rewrites its
    # source evidence.  Do not emit replay IDs/timestamps: those are unrelated
    # entropy, not explanatory evidence.
    with tempfile.TemporaryDirectory(prefix="flowness-ledger-scenario-") as temporary:
        replay_dir = Path(temporary) / "ledger"
        replay_dir.mkdir()
        shutil.copyfile(source_ledger_path, replay_dir / "ledger.jsonl")
        replay = Ledger.open(replay_dir)
        projection_before = rebuild_type_projection(replay)
        if read_fresh_type_projection(replay) != projection_before:
            raise LedgerError("fresh replay projection could not be read")
        replay.begin_proposal("P-scenario-pending", {"purpose": "scenario negative path"})
        try:
            read_fresh_type_projection(replay)
        except LedgerError as exc:
            stale_error = str(exc)
        else:  # pragma: no cover - contract guard
            raise LedgerError("scenario replay did not reject a stale projection")
        try:
            build_review_verdict(replay, "P-scenario-pending")
        except LedgerError as exc:
            pending_error = str(exc)
        else:  # pragma: no cover - contract guard
            raise LedgerError("scenario replay did not reject a pending verdict")
        projection_after = rebuild_type_projection(replay)

    if "stale" not in stale_error or "terminal" not in pending_error:
        raise LedgerError("scenario negative path returned an unexpected refusal")
    source_events = _event_rows(source_ledger)
    committed_types = [row["payload"]["type"] for row in source_ledger.read("committed")]
    recovery = demo["observations"]["recovery"]
    return {
        "source": {
            "demo_manifest_sha256": demo["manifest_sha256"],
            "ledger_sha256": _hash_file(source_ledger_path),
            "recovery_report_sha256": _hash_file(source_recovery_path),
            "recovery_report_hash": recovery["report_hash"],
        },
        "accepted_path": {
            "committed_types": committed_types,
            "review_verdict": accepted,
        },
        "rejected_and_conflict_path": {
            "rejected_record_invisible": demo["invariants"]["rejected_record_is_invisible"],
            "conflicting_decision_rejected": demo["invariants"]["conflict_is_rejected"],
            "conflicting_decision_error": demo["observations"]["conflicting_decision_error"],
            "review_verdict": rejected,
        },
        "recovery_path": {
            "read_refused_before_recovery": demo["invariants"]["tail_requires_recovery"],
            "read_refusal_error": demo["observations"]["interrupted_read_error"],
            "recovery_action": recovery["action"],
            "affected_bytes": recovery["affected_bytes"],
            "recovery_receipt_verified_by_demo": True,
        },
        "projection_path": {
            "initial_committed_type_counts": projection_before["committed_type_counts"],
            "stale_read_refused_after_pending_event": True,
            "stale_read_error": stale_error,
            "rebuilt_counts_after_pending_event": projection_after["committed_type_counts"],
            "watermark_changed_after_pending_event": projection_before["watermark"] != projection_after["watermark"],
        },
        "major_verdict_negative_path": {
            "pending_verdict_refused": True,
            "pending_verdict_error": pending_error,
            "accepted_label": accepted["verdict"],
            "rejected_label": rejected["verdict"],
            "meaning": "review verdict describes immutable terminal state; it does not authorize or execute an action",
        },
        "audit_timeline": source_events,
    }


def _mermaid(facts: dict[str, Any]) -> str:
    source = facts["source"]
    return "\n".join(
        [
            "%% Flowness Ledger Core public Open Alpha scenario timeline.",
            "%% Derived from a verified local demo; not runtime, benchmark, or production evidence.",
            "sequenceDiagram",
            "  participant C as Caller / demo",
            "  participant L as Local JSONL ledger",
            "  participant P as Local projection",
            "  participant V as Read-only review verdict",
            "  participant R as Recovery receipt",
            f"  Note over C,R: Input demo hash {source['demo_manifest_sha256']}",
            "  C->>L: P-accepted proposed records",
            "  L-->>C: pending committed view is empty",
            "  C->>L: immutable accepted decision",
            "  L-->>C: change.requested + artifact.checked visible",
            "  C->>V: terminal accepted review",
            "  V-->>C: accepted_committed (description only)",
            "  C->>L: P-rejected decision; later conflicting accept attempted",
            "  L-->>C: rejected record hidden; conflict refused",
            "  C->>V: terminal rejected review",
            "  V-->>C: rejected_not_committed (description only)",
            "  C->>L: incomplete final JSONL tail (demo injection)",
            "  L-->>C: read refuses",
            "  C->>L: bounded recover",
            "  L->>R: self-hashed recovered-prefix receipt",
            "  C->>P: build committed-type projection",
            "  C->>L: isolated replay adds pending proposal",
            "  P-->>C: stale projection read refused; rebuild required",
            "  C->>V: isolated replay asks pending verdict",
            "  V-->>C: terminal-decision refusal",
            "",
        ]
    )


def _markdown(facts: dict[str, Any]) -> str:
    source = facts["source"]
    timeline_rows = "\n".join(
        f"| {row['sequence']} | `{row['proposal_id']}` | `{row['kind']}` | `{row['payload_type'] or ''}` | `{row['outcome'] or ''}` |"
        for row in facts["audit_timeline"]
    )
    return f"""# Ledger candidate demo scenario timeline

**Status:** `public_open_alpha_local_explanation`. This pack is mechanically
derived from a verified local `demo-run.json`; it is not a runtime trace,
performance or efficiency benchmark, external-adoption result, or production
incident.

## What this explains

- **General reader:** a durable draft must not masquerade as a finished
  decision; a rejected change remains inspectable but does not become visible
  as committed work.
- **Developer:** committed visibility, projection freshness, bounded
  incomplete-tail recovery, and read-only verdict refusal are local API
  behaviors with hash-checked inputs.
- **Professional reader:** this is an evidence-bounded candidate explanation;
  an `accepted` record and an `accepted_committed` verdict are not an identity,
  approval, authorization, business effect, or release decision.

## Verified input binding

| Input | SHA-256 |
| --- | --- |
| Demo manifest | `{source['demo_manifest_sha256']}` |
| Ledger stream after recovery | `{source['ledger_sha256']}` |
| Recovery receipt | `{source['recovery_report_sha256']}` |
| Recovery receipt self-hash | `{source['recovery_report_hash']}` |

## Before / after: source event and committed visibility timeline

| Sequence | Proposal | Event kind | Payload type | Outcome |
| ---: | --- | --- | --- | --- |
{timeline_rows}

**Before terminal acceptance:** the committed reader is empty. **After
acceptance:** `{', '.join('`' + value + '`' for value in facts['accepted_path']['committed_types'])}`
is visible together. The self-hashed terminal description is
`{facts['accepted_path']['review_verdict']['verdict']}`; it describes records,
not approval authority.

## Negative paths that remain visible

- **Rejected + conflicting decision:** rejected record stays invisible:
  `{facts['rejected_and_conflict_path']['rejected_record_invisible']}`; a later
  conflicting terminal decision is refused:
  `{facts['rejected_and_conflict_path']['conflicting_decision_error']}`. The
  rejected terminal description is
  `{facts['rejected_and_conflict_path']['review_verdict']['verdict']}`.
- **Crash-tail recovery:** a read refuses before recovery:
  `{facts['recovery_path']['read_refusal_error']}`. The local recovery action
  is `{facts['recovery_path']['recovery_action']}` on only the incomplete final
  tail (`{facts['recovery_path']['affected_bytes']}` affected bytes), with its
  recovered-prefix receipt verified by the input demo.
- **Projection freshness:** the isolated copy first counts
  `{json.dumps(facts['projection_path']['initial_committed_type_counts'], sort_keys=True)}`.
  After an isolated pending event its old projection refuses:
  `{facts['projection_path']['stale_read_error']}`. Rebuild preserves the
  committed counts but changes the watermark:
  `{facts['projection_path']['watermark_changed_after_pending_event']}`.
- **Major verdict refusal:** an isolated pending proposal cannot yield a
  positive-looking verdict: `{facts['major_verdict_negative_path']['pending_verdict_error']}`.
  `{facts['major_verdict_negative_path']['meaning']}`.

## Diagram source

See [`timeline.mmd`](timeline.mmd). It is a readable Mermaid source, not a
claim of a deployed multi-agent sequence.

## Architecture and mechanism reading path

- [D0–D2: problem, local path, states, failures and recovery](LEDGER_CANDIDATE_ARCHITECTURE_D0_D2.md)
- [D3–D5: local planes, event boundary and sequences](LEDGER_CANDIDATE_ARCHITECTURE_D3_D5.md)
- [MECH-EVT-001: committed-visible ledger](../../oss/flowness-oss-harness/docs/mechanism-cards/v0/MECH-EVT-001.md)
- [MECH-PROJ-001: replayable projections and watermarks](../../oss/flowness-oss-harness/docs/mechanism-cards/v0/MECH-PROJ-001.md)
- [MECH-REVIEW-001: review lifecycle boundary](../../oss/flowness-oss-harness/docs/mechanism-cards/v0/MECH-REVIEW-001.md)
- [Candidate casebook](LEDGER_CANDIDATE_CASEBOOK.md) and
  [technical report](LEDGER_CANDIDATE_TECHNICAL_REPORT.md)

## What this pack cannot prove

It cannot prove a live Flowness service, real agent behavior, external consumer
use, role separation, authorization, production recovery, distributed
concurrency, exactly-once delivery, availability, performance, a benchmark,
the project-level clean-room receipt, export-rights record, or release
authorization. A changed source demo or code snapshot requires a new verified
pack and a content-graph review; this public artifact remains a local
explanation, not production evidence.
"""


def create_demo_scenario_pack(demo_dir: Path | str, output_dir: Path | str) -> dict[str, Any]:
    """Create a new deterministic scenario pack from a verified demo directory."""

    destination = _new_empty_directory(output_dir)
    facts = _derive_verified_facts(Path(demo_dir))
    timeline = {
        "schema_version": "flowness-ledger-core-demo-scenario-timeline/v1",
        "candidate_boundary": BOUNDARY,
        **facts,
        "not_proven": [
            "runtime or production behavior",
            "performance, efficiency, or comparator benchmark",
            "external adoption or a wider clean-room compatibility matrix",
            "this output is not the sealed-export, rights, or release-authorization record",
            "identity, authorization, or business-effect semantics",
        ],
    }
    _write_json(destination / TIMELINE_FILE, timeline)
    (destination / MARKDOWN_FILE).write_text(_markdown(facts), encoding="utf-8")
    (destination / MERMAID_FILE).write_text(_mermaid(facts), encoding="utf-8")
    artifacts = {
        TIMELINE_FILE: _hash_file(destination / TIMELINE_FILE),
        MARKDOWN_FILE: _hash_file(destination / MARKDOWN_FILE),
        MERMAID_FILE: _hash_file(destination / MERMAID_FILE),
    }
    unsigned = {
        "schema_version": SCHEMA,
        "candidate_boundary": BOUNDARY,
        "source": facts["source"],
        "artifacts": artifacts,
        "not_proven": timeline["not_proven"],
    }
    pack = {**unsigned, "pack_sha256": _hash_bytes(_canonical(unsigned))}
    _write_json(destination / PACK_FILE, pack)
    return pack


def verify_demo_scenario_pack(demo_dir: Path | str, output_dir: Path | str) -> dict[str, Any]:
    """Fail closed if the input demo or any derived scenario artifact changed."""

    destination = Path(output_dir).resolve(strict=True)
    pack_path = _regular_child(destination, PACK_FILE)
    try:
        pack = json.loads(pack_path.read_bytes())
    except json.JSONDecodeError as exc:
        raise LedgerError("scenario pack is not valid JSON") from exc
    if not isinstance(pack, dict):
        raise LedgerError("scenario pack is not an object")
    unsigned = {key: value for key, value in pack.items() if key != "pack_sha256"}
    if (
        pack.get("schema_version") != SCHEMA
        or pack.get("candidate_boundary") != BOUNDARY
        or pack.get("pack_sha256") != _hash_bytes(_canonical(unsigned))
    ):
        raise LedgerError("scenario pack self hash is invalid")
    artifacts = pack.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {TIMELINE_FILE, MARKDOWN_FILE, MERMAID_FILE}:
        raise LedgerError("scenario pack artifact set is invalid")
    for name, expected_hash in artifacts.items():
        if not isinstance(expected_hash, str) or _hash_file(_regular_child(destination, name)) != expected_hash:
            raise LedgerError(f"scenario pack {name} hash does not match")

    facts = _derive_verified_facts(Path(demo_dir))
    if pack.get("source") != facts["source"]:
        raise LedgerError("scenario pack input demo does not match")
    timeline_path = _regular_child(destination, TIMELINE_FILE)
    try:
        timeline = json.loads(timeline_path.read_bytes())
    except json.JSONDecodeError as exc:
        raise LedgerError("scenario timeline is not valid JSON") from exc
    expected_timeline = {
        "schema_version": "flowness-ledger-core-demo-scenario-timeline/v1",
        "candidate_boundary": BOUNDARY,
        **facts,
        "not_proven": pack["not_proven"],
    }
    if timeline != expected_timeline:
        raise LedgerError("scenario timeline does not match verified demo facts")
    if _regular_child(destination, MARKDOWN_FILE).read_text(encoding="utf-8") != _markdown(facts):
        raise LedgerError("scenario markdown does not match verified demo facts")
    if _regular_child(destination, MERMAID_FILE).read_text(encoding="utf-8") != _mermaid(facts):
        raise LedgerError("scenario Mermaid does not match verified demo facts")
    return pack
