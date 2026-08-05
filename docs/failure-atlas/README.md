# Flowness Failure Atlas

> **Stronger models improve nodes. They do not automatically repair the structure through which work moves.**

The Failure Atlas is the empirical companion to Flow Engineering. It collects structural failures observed or reproduced in agentic software work, the conditions that make them possible, the mechanism intended to address them, and the remaining boundary.

It is not a list of embarrassing bugs. It is a set of cases from which reusable engineering contracts can be extracted.

## 1. Every case should contain

```yaml
case_id: FA-001
title: Signal enters the ledger but has no consumer
status: DOGFOOD | REPLAYABLE | DESIGNED
source_commit: ...
fixture_hash: ...

symptom: ...
world_before: ...
trigger: ...
expected_flow: ...
actual_flow: ...

failure_mechanism:
  family: continuity
  missing_contract:
    - consumer
    - expectation
    - timeout
    - dead-letter

impact:
  time: ...
  tokens: ...
  human_load: ...
  risk: ...

evidence:
  - exact event range
  - artifact refs
  - screenshots / traces

mechanism:
  name: reconcile-loop
  mode_off_result: ...
  mode_on_result: ...

counterexamples:
  - when the signal is archival-only

remaining_boundary: ...
```

## 2. Seven pathology families

| Family | Core question |
|---|---|
| Formation | Why did an intent or signal fail to become executable Work? |
| Continuity | Why did live Work silently stop or lose its wake-up path? |
| Integrity | Why did identity, version, meaning, provenance, or information boundary drift? |
| Adaptation | Why did changed reality fail to invalidate and recompile downstream work? |
| Commitment | Why did a correct local output fail to enter its real consumer or target? |
| Closure | Why did the system declare completion without sufficient effect, evidence, or acceptance? |
| Learning | Why did the same structural failure recur without improving future infrastructure? |

## 3. Case quality levels

### L0 — Anecdote

A described symptom with no bound artifact. Useful as a lead, not evidence.

### L1 — Dogfood trace

Bound to private or sanitized event/artifact evidence. Valuable but not independently reproducible.

### L2 — Public replay fixture

A clean clone can reproduce the failure deterministically.

### L3 — Mechanism ablation

The failure appears with a mechanism disabled and is caught or repaired when enabled.

### L4 — Cross-implementation replication

An independent provider reproduces the same structural distinction.

## 4. Initial public case candidates

1. **Green gates, empty delivery** — local checks pass; the artifact never enters the real delivery bundle.
2. **Certificate for a nonexistent version** — self-review binds to a pre-merge draft.
3. **Heartbeat into the void** — high-volume signals have no actionable consumer.
4. **Dispatch and die** — task dispatch creates no monitored expectation or recovery path.
5. **Replan event without route** — a valid recovery event is committed but not consumed.
6. **Private coordinate contamination** — unfiltered source data crosses a publication boundary.
7. **Implemented but never activated** — code and tests pass, but no organic consumer uses the path.
8. **Same session mistaken for independence** — identity proves sameness, not separation of reviewers.
9. **Stale context after upstream change** — downstream work remains green after its assumptions are superseded.
10. **Validator reads the wrong fact** — a gate is syntactically active but semantically constant.

## 5. Publishing discipline

- Remove personal or secret data while preserving causal structure.
- Do not fabricate exact counts or costs when the source is incomplete.
- Bind every public replay case to source commit and fixture hash.
- Keep “observed,” “inferred,” and “designed” separate.
- Publish counterexamples where the mechanism should not fire.
- Include mechanism cost and false-positive behavior.
- Invite external cases that disprove the taxonomy or reveal missing families.

## 6. Community entry point

The Failure Atlas should be the easiest way for an experienced builder to contribute without adopting the whole runtime.

A contribution can be:

- a replayable failure fixture;
- an independent reproduction;
- a counterexample;
- a simpler mature mechanism;
- a metric;
- a case from another domain;
- a correction to the failure classification.

The desired culture is not “prove Flowness right.” It is “make structural agent failure easier to see, reproduce, and engineer against.”
