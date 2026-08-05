# Public API and Comment Standard

The public API should make Flow-first semantics visible. Comments should explain contracts and failure boundaries, not narrate obvious syntax.

## 1. Public API principles

### Stable subject

Public commands should begin from `Work`, the stable query surface for Flow's current state, not from hidden sessions.

Preferred:

```bash
flowness work show W-42
flowness work next W-42
flowness work explain W-42 --why-blocked
```

Secondary:

```bash
flowness execution inspect E-91
flowness graph show G-42-v7
```

### Exactness

Every mutating or evidentiary API should bind:

- exact object identifier;
- exact version / sequence cutoff;
- source commit where relevant;
- artifact hash;
- authority principal;
- idempotency key.

### Explicit uncertainty

Prefer states such as `unknown`, `suspect`, `stale`, `blocked`, and `abstain` over fabricated certainty.

### Orthogonal reality states

Do not expose one overloaded `done` boolean. Expose built, integrated, activated, accepted, and closed separately.

### Recoverable errors

Errors should include:

- stable code;
- human explanation;
- affected Work;
- retryability;
- recommended Reflow layer;
- evidence references;
- next action.

## 2. Module docstring template

```python
"""<One-sentence responsibility>.

Authoritative inputs:
    <events, projection, exact files, external readback>

Produces:
    <events, projection mutations, artifacts, decisions>

Owns:
    <state this module is authoritative for>

Does not own:
    <nearby state that belongs elsewhere>

Consumers:
    <who uses each output>

Correctness boundary:
    <invariants and what is deliberately not guaranteed>

Failure and recovery:
    <fail-closed/open behavior, idempotency, replay, dead-letter>

Authority:
    <physical/model/human tier and bypass rules>

Maturity:
    [RUNNABLE|INSPECTABLE|DOGFOOD|DESIGNED|OPEN QUESTION]
"""
```

## 3. Function contract template

```python
def compile_capsule(work_ref: WorkRef, as_of_seq: int) -> Capsule:
    """Compile a bounded execution view from committed state.

    Preconditions:
        - ``as_of_seq`` is committed-visible.
        - all requested projections can satisfy the freshness contract.

    Postconditions:
        - every source is at the same logical cutoff;
        - the capsule has a deterministic content hash;
        - active obligations are bound to exact scope and versions;
        - no post-cutoff event is silently included.

    Raises:
        ProjectionStale: freshness cannot be satisfied.
        UnresolvedReference: exact subject identity cannot be established.

    Does not guarantee:
        - that semantic judgments inside the capsule are correct;
        - that an agent will use the capsule correctly.
    """
```

## 4. Comment taxonomy

Use prefixes when useful:

```text
INVARIANT: property that must always hold
AUTHORITY: who may decide or mutate this state
EVIDENCE: source that makes a claim trustworthy
CONSUMER: downstream component that must receive this output
LIVENESS: expectation, timeout, wake-up, or dead-letter behavior
REFLOW: what becomes stale and where work should reopen
BOUNDARY: what this code deliberately does not claim
DOGFOOD: private operational behavior not yet public-proof bound
HISTORY: why an unusual design exists; include incident reference
```

Example:

```python
# LIVENESS: Dispatch is not complete until an ExecutionStarted,
# ExecutionRejected, or timeout event discharges this expectation.
```

## 5. Comments to avoid

Avoid comments that:

- say “ensure” without naming the invariant and enforcement tier;
- say “done” without the exact state transition;
- call a model answer “evidence” when it is only a claim;
- claim independence merely because session IDs differ;
- call a demo invocation “production activation”;
- imply a planned daemon is running;
- copy outdated design prose into code without version linkage.

## 6. Schema evolution

Public schemas need:

- `schema_version`;
- compatibility policy;
- migration notes;
- supersede relation;
- fixture for the old and new version;
- unknown-version behavior;
- exact release binding.

Immutable truth events should generally fail replay on unknown schema versions rather than being silently ignored. Derived views may have different recovery policies, but the distinction must be explicit.

## 7. Release-bound documentation

Every release should publish:

- source commit SHA;
- artifact SHA-256 manifest;
- generated docs version;
- schema versions;
- demo fixture versions;
- benchmark provider versions;
- claim status snapshot.

The renderer and README should never present an unverified directory or mutable branch as an exact release artifact.
