# Five-Layer Flowness Architecture

The five-layer model is the canonical product architecture for explaining Flowness to technical decision-makers. It expresses responsibilities, not an immutable folder layout.

```text
┌───────────────────────────────────────────────────────────┐
│ 1. Human Constitution                                     │
│ goals · ontology · judgments · obligations · authority    │
│ red lines · acceptance · promotion rules                  │
├───────────────────────────────────────────────────────────┤
│ 2. Persistent Work State & Truth                          │
│ event log · work identity · versions · projections        │
│ supersede history · provenance · expectations             │
├───────────────────────────────────────────────────────────┤
│ 3. Flow Compiler                                          │
│ current-world projection · context capsule · capabilities │
│ graph snapshot · policies · validators · completion       │
├───────────────────────────────────────────────────────────┤
│ 4. Flow Runtime                                           │
│ ready-set · dispatch · reconcile · liveness · dead-letter │
│ recovery · reflow · invalidation · scheduling             │
├───────────────────────────────────────────────────────────┤
│ 5. Reality & Assurance                                    │
│ artifacts · consumers · integration · activation · effect │
│ findings · independent review · acceptance · closure      │
└───────────────────────────────────────────────────────────┘
```

## 1. Human Constitution

This layer is the normative core of ming (命) — the authored part of the grammar that generates valid paths (ming as a whole also inherits the work's irreversible structure).

This layer answers:

- What does the owner value?
- What is the exact domain ontology?
- Which obligations are binding?
- What can the system decide by itself?
- What requires authority?
- Which effects are irreversible?
- Who accepts the result?
- How may learned behavior be promoted?

Typical objects:

- Goal / AntiGoal;
- Requirement;
- JudgmentCase and counterexample;
- Obligation;
- Policy and Redline;
- Authority / OwnerDecision;
- AcceptanceContract;
- PromotionRule.

**Invariant:** a model may propose changes to this layer, but may not silently become its authority.

## 2. Persistent Work State & Truth

This layer answers:

- What happened?
- Which object and version did it happen to?
- What is current as of which sequence number?
- What is waiting, blocked, stale, or superseded?
- What evidence and provenance exist?
- Can the current state be rebuilt after process failure?

Typical mechanisms:

- append-only event log;
- event schemas and provenance;
- committed-visible stream;
- sequence numbers and watermarks;
- projections and snapshots;
- exact version / supersede chains;
- WorkView;
- expectation and timeout records.

**Invariant:** transient agent memory is not authoritative Work state.

## 3. Flow Compiler (ming 命 × yun 运 → temporary Execution Assembly)

This layer answers:

- Given the current world, what execution is valid now?
- What is the minimum sufficient context?
- Which capabilities are needed?
- What graph and dependencies should be instantiated?
- Which obligations and validators apply?
- What completion evidence must be produced?

Ming and yun compile into an Execution Assembly. When that bounded assembly acts, a temporary Agent instance takes form. The Agent is the occurrence of agency expressed by the assembly, not any executor inside it.

Typical outputs:

```yaml
execution_assembly:
  work_ref: W-42
  fact_cutoff: 18420
  capsule_hash: sha256:...
  capabilities:
    - repository-edit
    - integration-test
  graph_snapshot: G-42-v7
  obligations:
    - organic-activation-required
  validators:
    - consumer-coverage
    - independent-review
  reflow_routes:
    integration_missing: repair
    capacity_assumption_failed: re_engineer
```

**Invariant:** every compiled execution is bound to authoritative source state and a reproducible cutoff.

## 4. Flow Runtime

This layer answers:

- What is ready now?
- What can run in parallel?
- Which execution should start?
- What happens when an executor dies?
- What signal is expected, by whom, and by when?
- How is silent dead flow detected?
- Which layer should reopen after failure?

Typical mechanisms:

- ready-set computation;
- dispatch and resource arbitration;
- session lifecycle and recovery;
- desired-state / observed-state reconciliation;
- expectations, timeouts, liveness checks, and dead letters;
- Finding routing;
- invalidation cascade;
- reflow orchestration;
- bounded human escalation.

**Invariant:** lack of an active Agent does not erase or orphan live Work.

## 5. Reality & Assurance

This layer answers:

- What changed outside the agent’s narrative?
- Was the artifact integrated into the target system?
- Did a real consumer invoke it?
- What independent evidence validates the result?
- Has the responsible owner accepted it?
- Can the system distinguish test, demo, deployment, organic activation, and acceptance?

Useful state separation:

```text
Built → Integrated → Activated → Accepted
```

Typical mechanisms:

- artifact identity and release binding;
- consumer coverage;
- CI and runtime readback;
- activation evidence;
- independent review;
- persistent Findings;
- targeted rework;
- fresh verdicts;
- owner acceptance;
- finality and closure.

**Invariant:** a producer cannot create the authoritative evidence that its own claim requires unless the evidence source is independently trusted and bound.

## Cross-layer flows

### Forward flow

```text
human intent
→ persistent Work
→ compiled execution
→ runtime action
→ real effect and evidence
→ new Work state
```

### Reflow

```text
finding or changed world
→ invalidate assumption / version
→ compute affected slice
→ update Work state
→ recompile execution
→ fresh validation
```

### Learning flow

```text
repeated traces and failures
→ candidate judgment / rule / validator
→ shadow measurement
→ human promotion
→ updated constitution or compiler
```

## Implementation maturity

The architecture is intentionally broader than the current public Open Alpha. Use the project’s status labels on every implementation claim:

- `[RUNNABLE]` public reproducible behavior;
- `[INSPECTABLE]` public code or contracts;
- `[DOGFOOD]` sustained private use or observation;
- `[DESIGNED]` specified but not connected;
- `[OPEN QUESTION]` research target.
