"""M-0.6 §5 Protocol-level invariant bootstrap.

# spec source:
#   03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#     §5.1 trigger point (L600..L615) — invoked by `towow init`
#     §5.2 4 bootstrap obligations canonical yaml literals (L617..L734)
#     §5.3 evolve allowed / retire forbidden (L736..L744)
#     §9 物理存储 (L1185..L1202) — bootstrap_obligations.yaml is the on-disk definition list
#
# RUN-038 Cap4: the 4 §5.2 obligation definitions are data (bootstrap_specs.BOOTSTRAP_OBLIGATION_SPECS),
# materialized to `.towow/obligation-system/bootstrap_obligations.yaml` by storage.py. When a
# towow_dir with that yaml is given, bootstrap_protocol_invariants reads the yaml (§9 — the yaml IS
# the definition list, not a Python hardcode). With no towow_dir / no yaml it falls back to the
# built-in specs (byte-for-byte the prior behavior), so existing `towow init` call sites are unchanged.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from towow.l0.obligations.bootstrap_specs import (
    BOOTSTRAP_OBLIGATION_SPECS,
    BootstrapObligationSpec,
)
from towow.schemas.enums import (
    ActorType,
    BaseClassification,
    EventCategory,
    EventType,
    ObligationCaptureSource,
    ObligationFieldsLifecycleState,
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
    SubjectEntityType,
    SubjectRole,
)
from towow.schemas.event_intent import EventIntent, ProvenanceHint, Subject, Supersede

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_OBLIGATIONS_COUNT = 4


class ProtocolInvariantImmutabilityError(Exception):
    """Raised when attempting to retire a system_bootstrap obligation (M-0.6 §5.3)."""


def _make_bootstrap_intent(
    *,
    obligation_id: str,
    nature: ObligationNature,
    severity: ObligationSeverity,
    scope_type: ObligationScopeType,
    scope_rule: str,
    definition: str,
    attached_to: list[dict[str, str]],
    mechanically_checkable: bool,
    checker_scope_hint: str | None = None,
) -> EventIntent:
    """Build a bootstrap ObligationCaptured EventIntent (capture_source=system_bootstrap)."""
    intent_uuid = uuid.uuid4().hex[:12]
    # RUN-068 §4.1.1: bootstrap captures carry the same capture_provenance + obligation_definition
    # blocks as runtime captures. They are the GENESIS exception — capture_source=system_bootstrap
    # with source_event_refs=[] is legitimate (§5.2; there is no upstream trigger event for a
    # protocol invariant). The runtime-capture gate enforcement (non-empty source_event_refs) does
    # NOT apply here — bootstrap goes through gate.bootstrap_commit, not attempt_commit.
    capture_provenance = {
        "capture_source": ObligationCaptureSource.SYSTEM_BOOTSTRAP.value,
        "source_event_refs": [],
        "captured_by_actor_id": "m06_bootstrap",
    }
    checker_metadata = {
        "mechanically_checkable": mechanically_checkable,
        "forbidden_pattern": None,  # §5.2: all 4 bootstrap obligations have forbidden_pattern=null
        "pattern_type": None,
        "checker_scope_hint": checker_scope_hint,
    }
    obligation_definition = {
        "nature": nature.value,
        "severity": severity.value,
        "scope_type": scope_type.value,
        "scope_rule": scope_rule,
        "definition": definition,
        "attached_to": attached_to,
        "checker_metadata": checker_metadata,
    }
    payload = {
        "obligation_id": obligation_id,
        "obligation_lifecycle_state": ObligationFieldsLifecycleState.CAPTURED.value,
        "attached_to": attached_to,
        "scope_rule": scope_rule,
        "scope_type": scope_type.value,
        "severity": severity.value,
        "nature": nature.value,
        "definition": definition,
        "capture_source": ObligationCaptureSource.SYSTEM_BOOTSTRAP.value,
        "mechanically_checkable": mechanically_checkable,
        "forbidden_pattern": None,
        "checker_scope_hint": checker_scope_hint,
        # RUN-068 §4.1.1 structured blocks
        "capture_provenance": capture_provenance,
        "obligation_definition": obligation_definition,
    }
    subjects = [
        Subject(
            entity_type=SubjectEntityType.OBLIGATION,
            entity_id=obligation_id,
            role=SubjectRole.PRIMARY,
        ),
    ]
    return EventIntent(
        local_intent_id=f"bootstrap-{obligation_id}-{intent_uuid}",
        event_type=EventType.OBLIGATION_CAPTURED,
        event_category=EventCategory.OBLIGATION,
        payload=payload,
        provenance_hint=ProvenanceHint(
            actor_type=ActorType.SYSTEM.value,
            actor_id="m06_bootstrap",
        ),
        base_classification=BaseClassification.IMMUTABLE_TRUTH,
        supersede=Supersede(is_supersede=False),
        subjects=subjects,
        schema_version="1.0.0",
    )


def _entry_to_spec(entry: dict[str, object]) -> BootstrapObligationSpec:
    """Convert a yaml `obligations[]` entry (storage.spec_to_yaml_entry shape) back to a spec.

    Tolerant of the yaml being hand-edited (Nature inspecting / tweaking the protocol invariant
    definitions §9). Enum fields are validated by the enum constructors; a malformed entry raises.
    """
    attached_raw = entry.get("attached_to", [])
    attached: list[tuple[str, str]] = []
    if isinstance(attached_raw, list):
        for a in attached_raw:
            if isinstance(a, dict):
                attached.append((str(a.get("entity_type")), str(a.get("entity_id"))))
    hint = entry.get("checker_scope_hint")
    return BootstrapObligationSpec(
        slug=str(entry["slug"]),
        nature=ObligationNature(str(entry["nature"])),
        severity=ObligationSeverity(str(entry["severity"])),
        scope_type=ObligationScopeType(str(entry["scope_type"])),
        scope_rule=str(entry["scope_rule"]),
        definition=str(entry["definition"]),
        attached_to=tuple(attached),
        mechanically_checkable=bool(entry.get("mechanically_checkable", False)),
        checker_scope_hint=str(hint) if isinstance(hint, str) else None,
    )


def _resolve_specs(towow_dir: Path | None) -> list[BootstrapObligationSpec]:
    """The bootstrap obligation definitions: from the on-disk yaml when present, else the built-in.

    M-0.6 §9 — when a towow_dir with `.towow/obligation-system/bootstrap_obligations.yaml` is given,
    that yaml IS the definition list bootstrap reads (not a Python hardcode). No towow_dir / no yaml
    → the built-in BOOTSTRAP_OBLIGATION_SPECS (byte-for-byte the prior behavior).
    """
    if towow_dir is not None:
        # Local import avoids a storage→bootstrap_specs→(this) import cycle at module load.
        from towow.l0.obligations.storage import load_bootstrap_obligation_entries

        entries = load_bootstrap_obligation_entries(towow_dir)
        if entries is not None:
            return [_entry_to_spec(e) for e in entries]
    return list(BOOTSTRAP_OBLIGATION_SPECS)


def bootstrap_protocol_invariants(towow_dir: Path | None = None) -> list[EventIntent]:
    """Produce ObligationCaptured EventIntents for v3 protocol-level invariants (M-0.6 §5.2).

    Definitions come from `.towow/obligation-system/bootstrap_obligations.yaml` when `towow_dir`
    is given and the yaml exists (§9 — the yaml is the真·source); otherwise from the built-in specs
    (byte-for-byte the prior hardcoded behavior — existing call sites pass no towow_dir). Caller
    (M-3.1 init CLI) submits via the bootstrap_commit special path on the commit gate.
    """
    short_uuid = uuid.uuid4().hex[:8]
    specs = _resolve_specs(towow_dir)
    return [
        _make_bootstrap_intent(
            obligation_id=f"{spec.slug}-{short_uuid}",
            nature=spec.nature,
            severity=spec.severity,
            scope_type=spec.scope_type,
            scope_rule=spec.scope_rule,
            definition=spec.definition,
            attached_to=[
                {"entity_type": etype, "entity_id": eid} for etype, eid in spec.attached_to
            ],
            mechanically_checkable=spec.mechanically_checkable,
            checker_scope_hint=spec.checker_scope_hint,
        )
        for spec in specs
    ]


def check_retire_allowed(capture_source: str) -> None:
    """M-0.6 §5.3: retire forbidden when capture_source=system_bootstrap."""
    if capture_source == ObligationCaptureSource.SYSTEM_BOOTSTRAP.value:
        raise ProtocolInvariantImmutabilityError(
            "Cannot retire a system_bootstrap obligation — protocol-level invariants are immutable "
            "(M-0.6 §5.3). Evolve allowed (with novelty), retire forbidden.",
        )
