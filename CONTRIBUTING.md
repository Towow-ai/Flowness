# Contributing to Flowness

Thank you for helping make agentic software work easier to understand, reproduce, and govern.

Flowness is not looking only for feature code. High-value contributions include failure fixtures, counterexamples, benchmarks, mechanism audits, documentation, adapters, translations, and simpler alternatives that replace custom machinery.

## Before opening a pull request

Choose the contribution class:

1. **Failure fixture** — reproducibly shows a structural failure.
2. **Mechanism change** — adds or changes runtime behavior.
3. **Benchmark provider** — implements a frozen evaluation interface.
4. **Documentation** — clarifies an existing public contract.
5. **Research / counterexample** — narrows, falsifies, or reframes a claim.
6. **Integration adapter** — connects Flowness to an external tool without bypassing truth or authority boundaries.
7. **Translation** — preserves canonical terminology and status claims.

For large architecture or schema changes, open an RFC Discussion before code.

## Contribution principles

### Start from the Work and the failure

A change should state:

- which Work-state question it helps answer;
- which observed or replayable failure motivates it;
- why a simpler mature mechanism is insufficient;
- which layer owns the state;
- who consumes the output.

### Keep claims bound to evidence

Use the project statuses:

- `[RUNNABLE]`
- `[INSPECTABLE]`
- `[DOGFOOD]`
- `[DESIGNED]`
- `[OPEN QUESTION]`

Do not turn “specified” into “supported” or “private dogfood” into “publicly proven.”

### Prefer canonical paths

Do not create parallel sources of truth, shadow orchestrators, or duplicate event routes merely to make a fixture green. Extend the existing authoritative path or explain why the architecture must change.

### Give every signal a future

Any event that is expected to advance Work needs:

- a consumer;
- an expectation;
- timeout or wake-up semantics where applicable;
- idempotency;
- a dead-letter or explicit archival-only outcome.

### Models recommend; infrastructure binds

Safety-critical decidable checks should not depend solely on a model. Human authority must not be synthesized by a controller or agent.

## Development setup

Follow the current repository README for the supported Python version and installation commands. Use a clean environment for qualification runs.

Before submitting:

```bash
# Run the public package tests and conformance checks defined by the repo.
# Exact commands should remain release-specific in README / CI.
```

Do not copy commands from this proposal if the repository has changed; the source commit and CI configuration are authoritative.

## Pull request requirements

A PR that changes behavior should include:

- a concise failure statement;
- scope and non-goals;
- exact objects and versions affected;
- the contract being added or changed;
- tests and at least one counterexample;
- migration and rollback where relevant;
- impact on event schemas and projections;
- consumers for new outputs;
- resource and operator-load impact;
- documentation and claim-register update;
- maturity status.

A mechanism PR should ideally include a **mechanism-off / mechanism-on** fixture.

## Commit and branch guidance

Use focused commits that keep code, tests, schemas, and docs reviewable. Avoid combining repository-wide formatting with semantic changes.

Suggested commit prefixes:

```text
flow:       Work/Flow public behavior
kernel:     L0 truth, projection, capsule, gate
runtime:    L2 dispatch, liveness, reconcile, reflow
semantics:  L1 goals, findings, judgments, activation
human:      L3 owner control surfaces
fixture:    Failure Atlas or benchmark fixture
docs:       public documentation
research:   formal or empirical artifacts
release:    manifests and release binding
```

## Review standard

Reviewers will ask:

1. What exact failure disappears?
2. What new failure could the mechanism create?
3. Is there a simpler existing technology?
4. Is the state authoritative or derived?
5. Is exact object/version identity preserved?
6. Does the change improve liveness without weakening integrity, or vice versa?
7. What happens on crash, replay, duplicate delivery, and stale context?
8. Who has authority to cross the final boundary?
9. Can a clean clone reproduce the claim?

## AI-assisted contributions

AI-generated code is welcome. The contributor remains responsible for:

- understanding the change;
- testing it;
- disclosing model/provider use when material to reproducibility;
- removing secrets and private context;
- verifying license compatibility;
- ensuring the PR description is not a fabricated account of work performed.

## License

By submitting a contribution, you agree that it is licensed under the repository’s Apache License 2.0 unless explicitly marked “Not a Contribution” or covered by a separate written agreement.
