# High-Assurance Software Engineering Flow Profile

Flowness’s first domain profile is a high-assurance route for ambiguous, non-trivial software work.

It is intentionally not required for every change. The Flow Compiler should select depth proportional to ambiguity, impact, reversibility, novelty, and evidence needs.

## 1. Full profile

```text
Intent / event
→ Investigation
→ Interview and requirements
→ Problem IR
→ Design alternatives and Design IR
→ Engineering research and Engineering IR
→ Engineering consensus
→ Dependency-aware plan
→ Isolated execution
→ Independent validation
→ Integration and activation evidence
→ Owner acceptance / closure
```

Any stage may produce a Finding that reopens the nearest invalid layer.

## 2. Why Problem, Design, Engineering, and Consensus are separate

### Problem IR

Owns:

- observed symptoms;
- evidence;
- hypotheses and competing explanations;
- scope and unknowns;
- problem statement suitable for design.

Does not own:

- the chosen system mechanism;
- the technical implementation.

### Design IR

Owns:

- actors, objects, relationships, states, permissions, and mechanisms;
- genuine alternatives;
- trade-offs and failure scenarios;
- quality scenarios;
- selected design and falsification conditions.

Does not own:

- concrete infrastructure choices unless they are themselves design constraints;
- deployment authority.

### Engineering IR

Owns:

- components, interfaces, data, concurrency, failure, capacity, security, migration, operations;
- source, prototype, benchmark, or production evidence for load-bearing decisions;
- rollback and reopen conditions.

Does not own:

- rewriting accepted human value or design intent for implementation convenience.

### Engineering Consensus

Owns:

- the stable, shared engineering facts that downstream tasks must consume;
- exact versions and consumers;
- invariants and validators.

It is **extracted from accepted Engineering IR**. It is not a substitute for doing design or engineering research.

## 3. Stage contracts

Every stage should declare:

```yaml
inputs:
  required: []
  optional: []
responsibility:
  owns: []
  must_not_own: []
outputs: []
completion_checks:
  mechanical: []
  semantic: []
evidence: []
consumers: []
reflow_routes: {}
```

A stage exists only if its output changes downstream behavior. Documents with no consumer are not stages; they are archives.

## 4. Proportional routes

### Fast path

For small, reversible, well-understood work:

```text
Goal → Plan → Execute → Test → Integrate
```

### Standard path

For cross-file features or moderate ambiguity:

```text
Goal → Problem/Requirements → Design sketch → Plan → Execute → Review → Integrate
```

### High-assurance path

For high impact, novel architecture, irreversible migration, security, or public claims:

```text
Full profile + independent evidence + owner gates + organic activation
```

The system should explain why a route was chosen.

## 5. Reflow routes

| Finding type | Preferred route |
|---|---|
| transient tool or environment failure | Re-execute |
| local implementation deviation | Repair |
| missing task or wrong dependency | Replan |
| capacity, interface, migration, or technical assumption failure | Re-engineer |
| object model, mechanism, authority, or quality-scenario failure | Redesign |
| wrong intent, value, scope, or acceptance definition | Re-interview |

The route is a recommendation until validated against evidence. A system should preserve uncertainty when multiple causes remain plausible.

## 6. Reality states

The software profile must not collapse:

```text
Implemented
Tested
Merged
Released
Integrated
Organically activated
Accepted
```

A result can pass earlier states and still fail the Flow.

## 7. Public proof target

A complete public organic E2E should demonstrate:

- a previously unseen repository goal;
- problem and requirements becoming stable enough for design;
- at least two genuinely different design alternatives;
- engineering decisions backed by source/prototype/benchmark evidence;
- a compiled task graph;
- at least two isolated executions;
- a real Finding that changes the graph or reopens an earlier layer;
- fresh validation over the successor state;
- merge and consumer wiring;
- organic activation or a clearly justified waiver;
- final owner acceptance;
- a replayable event/evidence package bound to a source commit and release hash.

Until such an artifact exists, Flowness should describe the complete profile as a direction supported by inspectable and dogfood mechanisms, not a fully public proven capability.
