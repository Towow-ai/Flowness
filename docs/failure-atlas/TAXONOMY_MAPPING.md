# Failure Taxonomy Mapping

Flowness currently has two useful classification styles. They should coexist rather than being forced into one hierarchy.

- **Empirical families** group incidents by how they appeared in dogfood and audit work.
- **Flow pathology families** group incidents by the underlying runtime property that failed.

The first is analogous to clinical departments. The second is analogous to pathology.

## Pathology families

| Flow family | Common visible symptoms | Typical mechanisms |
|---|---|---|
| Formation | signal logged but no Work/task; incomplete owner request; missing task derivation | event-to-work compiler, route completeness, prepared decision contract |
| Continuity | silent waiting; orphan execution; no consumer; no timeout; dead session | expectation, liveness monitor, reconcile, dead-letter, wake-up condition |
| Integrity | wrong version; semantic drift; secret leakage; false independence | exact refs, capsule cutoff, supersede chain, taint boundary, reviewer separation |
| Adaptation | stale graph; downstream green after upstream change; endless retry | invalidation cascade, suspect state, reflow router, graph recompilation |
| Commitment | orphan artifact; no release inclusion; no consumer wiring | consumer contract, release manifest, integration evidence |
| Closure | producer says done; synthetic signal treated as activation; old verdict reused | independent review, activation evidence, fresh verdict, owner acceptance |
| Learning | repeated incident; overfitted rule; no rollback | trace schema, candidate-rule lifecycle, shadow mode, counterexamples, promotion gate |

## Multi-label rule

One case may belong to several pathologies. For example:

> A Replan event is emitted, has no consumer, and the Work is later marked done.

This is simultaneously:

- Formation (the recovery Work was not formed);
- Continuity (the signal died);
- Closure (the parent Work closed falsely);
- Learning (if it repeats without a new route-completeness check).

Use one primary family for navigation and secondary tags for analysis.

## Why not call everything “semantic conservation”

Many failures are not meaning loss. Some are missing consumers, absent wake-up conditions, invalid technical assumptions, no integration, or authority boundaries. A broad “conservation law” vocabulary obscures the implementable mechanism.

The taxonomy should always lead to a question a contributor can test:

- Which producer emitted this?
- Which consumer was expected?
- What state transition should occur?
- What exact version was read?
- What fact did the gate inspect?
- What condition wakes the Work?
- What evidence proves effect?
