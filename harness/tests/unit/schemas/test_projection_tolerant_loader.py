"""Generic schema-drift-tolerant projection loader — unit guard for the recurring class:

    "a strict load of a derived projection crashes when historical events carry an old shape".

load_projection_tolerant is the single root-cause mechanism. These tests pin its CONTRACT:
  • dead enum value on a list item  → drop that item (legacy enum drift; schema evolved).
  • model-lagging extra field        → strip it (reducer stamps a field the model doesn't declare).
  • genuinely-missing required field → FAIL-CLOSED (re-raise as ProjectionDriftError) — masking a
    real defect is worse than crashing, and silently dropping a needed entry yields wrong analysis.
  • any other ValidationError        → FAIL-CLOSED.

Parametrized over EVERY strict projection State model so a future projection automatically inherits
both the tolerance and the fail-closed guarantee (and a regression in either is caught here).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from towow.schemas.projection_state import (
    AtReferenceGraphState,
    CommitHistoryProjection,
    ConceptGraphState,
    ConsumerRelationState,
    DetectionRuleLifecycleProjection,
    EscalationLifecycleProjection,
    FindingLifecycleProjection,
    ObligationLifecycleStateProjection,
    OwnershipState,
    ProjectionDriftError,
    TaskGraphState,
    load_projection_tolerant,
)

# Every strict projection State model + the (key, list-field) it exposes. A new projection added to
# projection_state.py should be added here too — that is the single place the guard is widened.
ALL_PROJECTION_MODELS: list[tuple[type[BaseModel], str]] = [
    (ConceptGraphState, "nodes"),
    (TaskGraphState, "nodes"),
    (ObligationLifecycleStateProjection, "obligations"),
    (OwnershipState, "entities"),
    (AtReferenceGraphState, "references"),
    (ConsumerRelationState, "relations"),
    (FindingLifecycleProjection, "findings"),
    (DetectionRuleLifecycleProjection, "rules"),
    (EscalationLifecycleProjection, "escalations"),
    (CommitHistoryProjection, "commits"),
]


class TestHappyPath:
    @pytest.mark.parametrize(("model", "list_field"), ALL_PROJECTION_MODELS)
    def test_empty_projection_loads(self, model: type[BaseModel], list_field: str) -> None:
        m = load_projection_tolerant(model, {list_field: []})
        assert getattr(m, list_field) == []

    @pytest.mark.parametrize(("model", "list_field"), ALL_PROJECTION_MODELS)
    def test_none_and_garbage_raw_load_as_empty(self, model: type[BaseModel], list_field: str) -> None:
        # daemons/CLI pass store.read(...) which can be None when the file is missing — must not crash.
        assert getattr(load_projection_tolerant(model, None), list_field) == []
        assert getattr(load_projection_tolerant(model, "not-a-dict"), list_field) == []
        assert getattr(load_projection_tolerant(model, 123), list_field) == []


class TestLegacyEnumDrift:
    """Population 1 — the owner's named class: an enum member was removed; old events carry it."""

    def test_task_graph_legacy_artifact_entity_type_ref_dropped(self) -> None:
        # SubjectEntityType has 'file', not 'artifact' (a 2026-05-23 planner stamped 'artifact').
        raw = {
            "nodes": [
                {
                    "task_id": "t1",
                    "task_type": "code_change",
                    "description": "n",
                    "status": "completed",
                    "read_set": [
                        {"entity_type": "file", "entity_id": "ok.go"},
                        {"entity_type": "artifact", "entity_id": "legacy.go"},  # dead enum
                    ],
                    "write_set": [{"entity_type": "artifact", "entity_id": "dead.go"}],  # dead enum
                },
            ],
            "edges": [],
        }
        m = load_projection_tolerant(TaskGraphState, raw)
        assert len(m.nodes) == 1
        # only the canonical 'file' ref survives in read_set; the dead-enum refs are dropped.
        assert [r.entity_id for r in m.nodes[0].read_set] == ["ok.go"]
        assert m.nodes[0].write_set == []

    def test_ownership_legacy_entity_type_entry_dropped(self) -> None:
        raw = {
            "entities": [
                {"entity_type": "task", "entity_id": "t1"},
                {"entity_type": "artifact", "entity_id": "x"},  # dead enum → drop this entry
            ],
        }
        m = load_projection_tolerant(OwnershipState, raw)
        assert [e.entity_id for e in m.entities] == ["t1"]

    def test_at_reference_legacy_endpoint_enum_drops_entry(self) -> None:
        # entity_type='spec_document'/'event' are not in SubjectEntityType — drop the whole entry.
        raw = {
            "references": [
                {
                    "reference_id": "r-ok",
                    "source": {"entity_type": "concept", "entity_id": "c1"},
                    "target": {"entity_type": "concept", "entity_id": "c2"},
                    "reference_type": "spec",
                },
                {
                    "reference_id": "r-legacy",
                    "source": {"entity_type": "spec_document", "entity_id": "d1"},  # dead enum
                    "target": {"entity_type": "event", "entity_id": "e1"},  # dead enum
                    "reference_type": "spec",
                },
            ],
        }
        m = load_projection_tolerant(AtReferenceGraphState, raw)
        assert [r.reference_id for r in m.references] == ["r-ok"]

    def test_multiple_dead_enum_items_in_one_list_all_dropped(self) -> None:
        raw = {
            "entities": [
                {"entity_type": "artifact", "entity_id": "a"},
                {"entity_type": "task", "entity_id": "keep"},
                {"entity_type": "spec_document", "entity_id": "b"},
            ],
        }
        m = load_projection_tolerant(OwnershipState, raw)
        assert [e.entity_id for e in m.entities] == ["keep"]


class TestModelLaggingExtraField:
    """Population 2a — the reducer stamps a field the strict model doesn't declare (model lag)."""

    def test_finding_extra_reducer_fields_stripped(self) -> None:
        # The finding reducer writes review_dimension/is_preexisting/verification_state/resolution/
        # closure_state — none declared on FindingLifecycleEntry. They are stripped on read.
        raw = {
            "findings": [
                {
                    "finding_id": "f1",
                    "lifecycle_state": "created",
                    "severity": "major",
                    "risk_surface": "x",
                    "detection_method": "automated_rule",
                    "created_at_event_id": "e1",
                    "last_state_change_event_id": "e1",
                    "review_dimension": None,  # extra
                    "is_preexisting": False,  # extra
                    "verification_state": None,  # extra
                    "resolution": None,  # extra
                    "closure_state": None,  # extra
                },
            ],
        }
        m = load_projection_tolerant(FindingLifecycleProjection, raw)
        assert len(m.findings) == 1
        assert m.findings[0].finding_id == "f1"
        assert not hasattr(m.findings[0], "review_dimension")

    def test_concept_node_extra_seed_fields_stripped(self) -> None:
        raw = {
            "nodes": [
                {
                    "concept_id": "c1",
                    "name": "n",
                    "definition": "d",
                    "created_by_stage": "interview",
                    "created_at_event_id": "e1",
                    "seed_origin": False,  # extra reducer field
                    "source_brief_id": "b1",  # extra reducer field
                },
            ],
            "edges": [],
        }
        m = load_projection_tolerant(ConceptGraphState, raw)
        assert m.nodes[0].concept_id == "c1"


class TestFailClosedOnGenuineDefect:
    """The honesty gate — non-drift defects must NOT be silently tolerated."""

    def test_missing_required_field_fails_closed(self) -> None:
        # consumer_relation live shape: consumers carry consumer_id (no entity_id) → entity_id is a
        # genuinely-missing REQUIRED field. Dropping it would empty a non-empty consumer list and
        # make a consumed concept look orphaned → must fail-closed, not tolerate.
        raw = {
            "relations": [
                {
                    "concept_id": "c1",
                    "consumers": [{"consumer_id": "M-1.2", "consumer_type": "skill", "usage": "x"}],
                },
            ],
        }
        with pytest.raises(ProjectionDriftError) as ei:
            load_projection_tolerant(ConsumerRelationState, raw)
        # original ValidationError is preserved for inspection (not swallowed).
        assert isinstance(ei.value.original, ValidationError)
        assert any(e["type"] == "missing" for e in ei.value.original.errors())

    def test_empty_min_length_string_fails_closed(self) -> None:
        # An empty required string (min_length=1) is a data-quality defect, NOT legacy enum/extra
        # drift → fail-closed.
        raw = {
            "commits": [
                {
                    "commit_event_id": "e1",
                    "envelope_event_id": "",  # min_length=1 violated
                    "verdict": "accepted",
                    "occurred_at_event_id": "e1",
                },
            ],
        }
        with pytest.raises(ProjectionDriftError):
            load_projection_tolerant(CommitHistoryProjection, raw)

    def test_wrong_type_fails_closed(self) -> None:
        # A list where a string is required, etc. — a real type error must surface.
        raw = {"nodes": [{"task_id": ["not", "a", "string"], "task_type": "code_change",
                          "description": "d", "status": "created"}], "edges": []}
        with pytest.raises(ProjectionDriftError):
            load_projection_tolerant(TaskGraphState, raw)

    def test_mixed_drift_and_genuine_defect_fails_closed(self) -> None:
        # If a payload has BOTH benign drift (extra field) AND a genuine defect (missing required),
        # the genuine defect wins → fail-closed (we never let a real bug ride along with drift).
        raw = {
            "relations": [
                {
                    "concept_id": "c1",
                    "consumers": [
                        {"consumer_id": "x", "consumer_type": "skill", "usage": "u",  # missing entity_id
                         "bogus_extra": 1},  # also an extra field
                    ],
                },
            ],
        }
        with pytest.raises(ProjectionDriftError):
            load_projection_tolerant(ConsumerRelationState, raw)


class TestNoFalseTolerance:
    """A clean projection must load through the tolerant loader byte-for-byte identically to a plain
    strict=False validate (the loader is a no-op on conforming data — zero behavioral surprise)."""

    def test_clean_data_identical_to_plain_validate(self) -> None:
        raw = {
            "obligations": [
                {
                    "obligation_id": "o1",
                    "lifecycle_state": "checked",
                    "canonical_state": "active",
                    "scope_rule": "r",
                    "scope_type": "global",
                    "severity": "red_line",
                    "nature": "invariant",
                    "definition": "d",
                    "created_at_event_id": "e1",
                    "last_state_change_event_id": "e1",
                },
            ],
        }
        tolerant = load_projection_tolerant(ObligationLifecycleStateProjection, raw)
        plain = ObligationLifecycleStateProjection.model_validate(raw, strict=False)
        assert tolerant == plain
