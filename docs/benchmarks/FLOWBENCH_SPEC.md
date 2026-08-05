# FlowBench v0.1 Qualification Specification

**Status:** `[DESIGNED]`  
**Purpose:** evaluate Flow-runtime properties that single-task code-generation benchmarks do not measure.

FlowBench should not ask “Which model writes the best patch?” It should ask:

> **Can a system keep Work alive, coherent, adaptable, connected to reality, and governed across multiple executions and changing conditions?**

## 1. Qualification before comparison

A provider must pass qualification before its score is compared. Qualification rejects experiments contaminated by:

- answer or case-label leakage;
- run-order contamination;
- evaluator feedback entering provider context;
- hidden controller substitution;
- post-hoc applicability selection;
- inconsistent object/version binding;
- non-frozen provider configuration;
- incomparable authority or information.

A failed qualification is not a low score. It is a disqualified measurement channel.

## 2. Frozen provider interface

Each provider receives only:

```yaml
work_packet:
  opaque_work_token: random
  initial_world: signed fixture bundle
  lawful_tools: frozen list
  authority: explicit capability set
  resource_budget:
    wall_time: ...
    tokens: ...
    compute: ...
  output_contract: event-and-evidence schema
```

It does not receive:

- expected route;
- expected partner/agent count;
- expected reflow label;
- hidden fault label;
- evaluator-private manifest hash;
- case-semantic IDs;
- prior-arm outputs.

## 3. Benchmark families

### F1 — Work survival

Inject:

- agent crash;
- process restart;
- context loss;
- provider replacement.

Measure:

- Work identity survival;
- recovery time;
- lost committed facts;
- duplicated side effects;
- human recovery actions.

### F2 — Continuity and silent dead flow

Inject:

- event with no consumer;
- prerequisite with no producer;
- dropped acknowledgement;
- missing timeout;
- dead-letter path;
- waiting external condition.

Measure:

- detection latency;
- correct `waiting` vs `orphaned` classification;
- false alarms;
- successful wake-up;
- unresolved-work rate.

### F3 — Context freshness and identity

Inject:

- post-cutoff event;
- stale concept version;
- ambiguous object name;
- floating file path;
- changed dependency.

Measure:

- stale-context rejection;
- exact object/version correctness;
- hidden dependency rate;
- cross-run reproducibility;
- context size and token cost.

### F4 — Integrity and drift

Inject:

- API semantic rename;
- contradictory state transition;
- changed invariant;
- data convention shift;
- security obligation change;
- docs/code divergence;
- reversal oscillation.

Measure:

- materialized drift rate;
- detection point;
- false accept / false reject;
- semantic-elision incidence;
- novelty padding;
- human escalation load.

### F5 — Reflow routing

Inject failures at different layers:

- transient execution;
- implementation bug;
- missing dependency;
- invalid capacity assumption;
- broken design mechanism;
- wrong owner intent.

Measure:

- route accuracy: re-execute / repair / replan / re-engineer / redesign / re-interview;
- affected-slice precision and recall;
- unnecessary work invalidated;
- time and tokens to recovery;
- reuse of stale verdicts.

### F6 — Commitment and reality

Inject:

- artifact not included in release;
- code not wired to consumer;
- deployed path not invoked;
- test/demo signal mislabeled as organic activation;
- output generated in the wrong repository or branch.

Measure:

- false closure rate;
- consumer coverage;
- activation evidence quality;
- exact release binding;
- time to materialized effect.

### F7 — Human governance

Inject:

- value conflict;
- ambiguous irreversible action;
- missing authority;
- revocation;
- explicit refusal;
- stale owner decision.

Measure:

- correct autonomy / ask / abstain / reject behavior;
- authority substitution rate;
- owner decision count;
- decision preparation quality;
- time to resolve;
- unauthorized effect rate.

### F8 — Learning without uncontrolled self-modification

Repeat structurally similar incidents.

Measure:

- recurrence reduction;
- candidate-rule precision;
- shadow-to-enforced promotion correctness;
- counterexample handling;
- rollback success;
- regression introduced by learned rules;
- human promotion burden.

## 4. Baselines

At minimum compare:

- B0: plain coding agent with repository and tests;
- B1: agent + persistent task list / issue tracker;
- B2: event log only;
- B3: event log + bounded fresh context;
- B4: graph orchestration with durable state;
- B5: strongest mature agent stack available at freeze time;
- B6: Flowness mechanism composition under test;
- B7: skilled human engineer or human team on a bounded subset where feasible.

Do not weaken a baseline by removing native capabilities. Do not grant any treatment authority or information it would not lawfully possess.

## 5. Primary metrics

FlowBench should report a vector, not one winner score:

```text
Task success
False closure
Silent dead-flow rate
Time to valid next step
Work survival
Stale-context use
Materialized drift
Reflow route accuracy
Affected-slice precision / recall
Organic activation correctness
Authority substitution
Human decision load
Tokens / wall time / compute
Maintenance complexity
```

A composite score may be provided only after the full vector and weighting rationale are published.

## 6. Evidence binding

Every run must bind:

- private evaluator manifest;
- provider-visible manifest;
- source commit;
- fixture version;
- provider image/configuration;
- model and harness version;
- random seeds where applicable;
- event and artifact manifests;
- exact resource budget;
- evaluator version;
- final report hash.

The provider-visible token must be random and independent of hidden labels or expected results.

## 7. Holdout discipline

- Freeze all schemas, routes, metrics, and provider interfaces before holdout.
- Do not expand feature columns or validators based on holdout behavior.
- Separate development, calibration, and fresh holdout data.
- Prevent hidden case identity from leaking through paths, process arguments, hashes, logs, or order.
- Sanitize parent process arguments before child execution.
- Use independent provider implementations where reproducibility is a claim.

## 8. Minimum publishable v0.1

The smallest useful release is not all eight families. It is:

1. F1 Work survival;
2. F2 silent dead flow;
3. F5 reflow routing;
4. F6 commitment / activation;
5. B0, B1, B4, B6;
6. 8–12 frozen fixtures;
7. one fresh holdout set;
8. complete artifact and resource accounting;
9. failure-by-channel reporting;
10. no aggregate winner claim until qualification passes.

## 9. Success and rejection criteria

FlowBench succeeds if it can distinguish systems by specific failure channels and reproduce those distinctions independently.

It should reject its own design if:

- fixture labels are recoverable;
- evaluator authority is substituted for treatment authority;
- only one prewritten route can pass;
- metrics reward paperwork rather than real effect;
- a stronger baseline solves the cases with a simpler mature mechanism;
- resource budgets make the comparison uninterpretable;
- provider implementation differences dominate the intended treatment.
