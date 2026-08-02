"""M-0.6 §5.2 bootstrap obligation 定义 + §6.4 resolver prompt 文本 — single source of truth (Cap4).

# spec source: 03-l0-truth-source/M-0.6-obligation-system-detailed-design.md
#   §5.2 (L617..L734) — the 4 v3 protocol-level invariant obligation definitions
#   §6.4.1 (L850..L898) — resolver system prompt 框架
#   §6.4.2 (L900..L944) — resolver user prompt 模板
#
# This module is the data source of truth for the bootstrap obligations + resolver prompts.
# storage.py materializes them to `.towow/obligation-system/` (bootstrap_obligations.yaml +
# resolver-prompts/*.md); bootstrap.py builds EventIntents from either the on-disk yaml (when
# present) or these built-in specs (the byte-for-byte default). Keeping the literals as data (not
# inlined in bootstrap.py) is what lets the yaml be the真·source bootstrap reads, not a hardcode.
"""

from __future__ import annotations

from dataclasses import dataclass

from towow.schemas.enums import (
    ObligationNature,
    ObligationScopeType,
    ObligationSeverity,
)


@dataclass(frozen=True)
class BootstrapObligationSpec:
    """One §5.2 protocol-level invariant definition (capture_source=system_bootstrap implied)."""

    slug: str  # obligation_id prefix; runtime appends a bootstrap uuid suffix
    nature: ObligationNature
    severity: ObligationSeverity
    scope_type: ObligationScopeType
    scope_rule: str
    definition: str
    attached_to: tuple[tuple[str, str], ...]  # (entity_type, entity_id) pairs
    mechanically_checkable: bool
    checker_scope_hint: str | None = None


BOOTSTRAP_OBLIGATION_SPECS: tuple[BootstrapObligationSpec, ...] = (
    BootstrapObligationSpec(
        slug="obl-intent-attribution",
        nature=ObligationNature.INTENT_ATTRIBUTION,
        severity=ObligationSeverity.RED_LINE,
        scope_type=ObligationScopeType.ALL_PATCHES,
        scope_rule=(
            "Applies to every task that produces or modifies an Artifact, Patch, Concept, "
            "TaskNode, or any persistent system entity. Does not apply to read-only queries "
            "or transient computations."
        ),
        definition=(
            "Every patch must be traceable to a Goal in the concept graph. The traceability "
            "chain may pass through Requirements, Decisions, or Tasks, but must terminate at "
            "a Goal node. Patches without a traceable Goal must be rejected."
        ),
        attached_to=(
            ("concept", "concept:patch"),
            ("concept", "concept:artifact"),
        ),
        mechanically_checkable=False,
    ),
    BootstrapObligationSpec(
        slug="obl-contract-preservation",
        nature=ObligationNature.CONTRACT,
        severity=ObligationSeverity.RED_LINE,
        scope_type=ObligationScopeType.ALL_PATCHES,
        scope_rule=(
            "Applies when a patch modifies an Artifact that has declared consumers in the "
            "concept graph (via @ reference or contract edges). The patch must not break "
            "existing consumer contracts unless evolved with explicit novelty."
        ),
        definition=(
            "Every modification to an Artifact must preserve its existing contracts with "
            "declared consumers (forward-slice from O-11). Breaking changes require a "
            "supersede event with explicit novelty declaration; silent breaking changes "
            "must be rejected."
        ),
        attached_to=(
            ("concept", "concept:artifact"),
            ("concept", "concept:contract"),
        ),
        mechanically_checkable=False,
    ),
    BootstrapObligationSpec(
        slug="obl-novelty-required-for-supersede",
        nature=ObligationNature.INVARIANT,
        severity=ObligationSeverity.RED_LINE,
        scope_type=ObligationScopeType.ALL_PATCHES,
        scope_rule="Applies to every patch with is_supersede=true.",
        definition=(
            "Every supersede event must declare novelty_type AND populate at least one of "
            "new_constraint / new_evidence / new_resolution / scope_change / nature_override. "
            "no_new_information=true is forbidden. This prevents oscillation A→A'→A→A'."
        ),
        attached_to=(("concept", "concept:supersede"),),
        mechanically_checkable=True,
        checker_scope_hint="M-0.5 NoveltyCheck §3.5.1 implements this",
    ),
    BootstrapObligationSpec(
        slug="obl-capsule-required-before-run",
        nature=ObligationNature.INVARIANT,
        severity=ObligationSeverity.RED_LINE,
        scope_type=ObligationScopeType.GLOBAL,
        scope_rule=(
            "Applies to every fork invocation in the system. Every fork must receive a "
            "capsule via M-0.3 capsule assembly pipeline; agents must not run without "
            "capsule (SYSTEM-REQUIREMENTS P-11)."
        ),
        definition=(
            "No agent fork may execute without a corresponding CapsuleCompiled event. "
            "Bypassing the capsule pipeline is forbidden."
        ),
        attached_to=(
            ("concept", "concept:fork"),
            ("concept", "concept:capsule"),
        ),
        mechanically_checkable=True,
        checker_scope_hint="M-0.5 EnvelopeIntegrity §3.0 implements this (capsule_compiled_event_id required)",
    ),
)


def spec_to_yaml_entry(spec: BootstrapObligationSpec) -> dict[str, object]:
    """Serialize a spec to the yaml entry shape storage.py writes + bootstrap.py reads back."""
    return {
        "slug": spec.slug,
        "nature": spec.nature.value,
        "severity": spec.severity.value,
        "scope_type": spec.scope_type.value,
        "scope_rule": spec.scope_rule,
        "definition": spec.definition,
        "attached_to": [
            {"entity_type": etype, "entity_id": eid} for etype, eid in spec.attached_to
        ],
        "mechanically_checkable": spec.mechanically_checkable,
        "checker_scope_hint": spec.checker_scope_hint,
    }


# ─── §9 config.json defaults (mirror schemas.resolver.ResolverConfig + prior hardcodes) ───
DEFAULT_RESOLVER_MODEL = "sonnet"
DEFAULT_RESOLVER_TIMEOUT_MS = 30000
DEFAULT_RESOLVER_CONTRACT_VERSION = "1.0.0"


# ─── §6.4.1 resolver system prompt (materialized verbatim from spec) ─────────────────────
SYSTEM_PROMPT_TEXT = """\
You are the ObligationActivationResolver for the ToWow horizontal runtime layer.

Your job: given a task and a list of candidate obligations, determine which
obligations apply to this specific task in this specific context.

You DO decide, for each candidate obligation:
  - hit: bool (does this obligation apply to this task?)
  - confidence: float in [0.0, 1.0]
  - reason: short explanation
  - evidence: references to relevant projection nodes or events

You DO NOT decide:
  - Whether the obligation itself is correct
  - Whether the agent will violate it (that's commit gate's job, not yours)
  - Whether the obligation should be retired or evolved
  - Any aspect outside scope hit/miss

Decision principles:
  1. RED-LINE obligations: when uncertain, conservative (hit=true, confidence may be
     low, but include). Missing a red-line is worse than over-including.
  2. NORMAL obligations: hit when scope_rule is genuinely matched by task context.
     If unsure, default to miss unless evidence is strong.
  3. HINT obligations: hit only when high confidence (>0.8). Hints are nudges, not
     requirements—false positives are costly to capsule density.
  4. scope_type=concept_neighborhood: mechanical filter already verified a path
     exists; you decide whether the path is semantically relevant for THIS task.
  5. scope_type=semantic_dependent: full semantic interpretation based on
     scope_rule + definition + task context.

Format requirements:
  - Output ONLY valid JSON matching the ResolverVerdict schema below
  - Do NOT output prose, markdown, or commentary outside the JSON
  - Every candidate obligation_id in the input MUST appear exactly once in your
    judgments array (no more, no less)
  - confidence must be a float in [0.0, 1.0]
  - When confidence < 0.7, populate the "uncertainty" field with a brief
    explanation of what makes you uncertain
"""


# ─── §6.4.2 resolver user prompt template (materialized verbatim from spec) ──────────────
USER_PROMPT_TEMPLATE_TEXT = """\
=== TASK SCOPE ===
task_id: {task_id}
task_type: {task_type}
target_artifacts: {target_artifacts}
declared_write_set: {declared_write_set}
touched_nodes: {touched_nodes}
{reentry_block}

=== PROJECTION SLICE ===

Concept Graph Neighborhood (N-hop expansion from touched_nodes):
{concept_graph_neighborhood}

{optional_graph_slices}

=== CANDIDATE OBLIGATIONS ===

{candidate_obligations}

=== TASK ===

For each candidate obligation above, judge whether it applies to this specific
task. Apply the decision principles from your system prompt.

Output strict JSON matching ResolverVerdict schema. No prose outside JSON.
"""


__all__ = [
    "BOOTSTRAP_OBLIGATION_SPECS",
    "DEFAULT_RESOLVER_CONTRACT_VERSION",
    "DEFAULT_RESOLVER_MODEL",
    "DEFAULT_RESOLVER_TIMEOUT_MS",
    "SYSTEM_PROMPT_TEXT",
    "USER_PROMPT_TEMPLATE_TEXT",
    "BootstrapObligationSpec",
    "spec_to_yaml_entry",
]
