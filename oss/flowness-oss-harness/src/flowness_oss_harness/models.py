from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

MechanismStatus = Literal[
    "legacy",
    "current_verified",
    "experimental",
    "designed_target",
    "blocked",
    "unknown",
    "written_only",
]
EvidenceKind = Literal[
    "code",
    "runtime",
    "test",
    "event",
    "schema",
    "commit",
    "transcript",
    "document",
    "external",
]
def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    kind: EvidenceKind
    locator: str
    source_snapshot_id: str
    content_hash: str
    captured_at: str
    independent_group: str
    summary: str = ""
    basis: str = ""
    observed_excerpt: str = ""
    hash_scope: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MechanismCard:
    mechanism_id: str
    public_name: str
    internal_names: list[str]
    problem_and_failure: str
    status: MechanismStatus
    triggers: list[str]
    inputs: list[str]
    outputs: list[str]
    authoritative_state: list[str]
    state_transitions: list[str]
    invariants: list[str]
    authority_and_permissions: list[str]
    producers: list[str]
    consumers: list[str]
    dependencies: list[str]
    failure_modes: list[str]
    recovery_and_rollback: list[str]
    inventory_item_ids: list[str]
    evidence: list[EvidenceRef]
    known_drift: list[str]
    public_scope: str
    public_claims: list[str]
    confidence: Literal["low", "medium", "high"]
    unresolved_questions: list[str]
    verification_trace: dict[str, list[str]] = field(default_factory=dict)
    schema_version: str = "mechanism-card/v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass
class ClaimRecord:
    claim_id: str
    text: str
    status: MechanismStatus
    scope: str
    baseline: str
    success_criteria: str
    evidence: list[EvidenceRef]
    known_limitations: list[str]
    prohibited_paraphrases: list[str]
    canonical_consumers: list[str]
    expires_at: str | None
    owner: str
    last_verified_at: str | None
    affected_artifacts: list[str]
    schema_version: str = "claim-record/v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass
class DriftFinding:
    drift_id: str
    surface_from: str
    surface_to: str
    source_locators: list[str]
    affected_consumers: list[str]
    severity: Literal["critical", "high", "medium", "low"]
    public_impact: str
    remediation_or_downgrade: str
    state: Literal["open", "accepted", "resolved", "disputed"]
    evidence: list[EvidenceRef] = field(default_factory=list)
    schema_version: str = "drift-finding/v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass
class UnknownRecord:
    unknown_id: str
    inventory_item_id: str
    object_type: str
    locator: str
    question: str
    blocking: bool
    evidence: list[EvidenceRef]
    next_check: str
    schema_version: str = "unknown-record/v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass
class TranscriptExcerpt:
    excerpt_id: str
    snapshot_id: str
    source_namespace: str
    session_alias: str
    record_uuid: str
    parent_uuid: str | None
    timestamp: str | None
    line_number: int
    block_index: int
    char_span: list[int]
    raw_record_sha256: str
    extracted_text_sha256: str
    origin_class: Literal[
        "human_confirmed", "human_probable", "ambiguous", "nonhuman"
    ]
    topic_labels: list[str]
    redaction_actions: list[str]
    text: str
    approved_for_public_quote: bool = False
    rule_version: str = "transcript-origin/v1"
    schema_version: str = "transcript-excerpt/v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
