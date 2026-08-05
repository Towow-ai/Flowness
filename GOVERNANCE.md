# Flowness Project Governance

Flowness is currently maintainer-led while its public contracts, benchmark discipline, and contributor base mature. This document describes how decisions should be made without pretending the project already has an institution it has not yet built.

## 1. Decision classes

### Routine maintenance

Examples: documentation corrections, test improvements, non-semantic refactors, dependency maintenance.

Decision: one maintainer approval after required checks.

### Public contract changes

Examples: event schemas, Work lifecycle, CLI compatibility, evidence status semantics, benchmark provider interface.

Decision: public RFC, migration analysis, at least two maintainer reviews, release note, and versioning decision.

### Safety / authority boundary changes

Examples: weakening a fail-closed check, changing who can accept or bypass, treating model output as authoritative evidence.

Decision: public RFC, threat/counterexample review, independent approval from a maintainer who did not author the change, and explicit release-gate approval.

### Category and claim changes

Examples: redefining Flow Engineering, comparative superiority claims, “first” or “only” claims, benchmark winner claims.

Decision: update the Claims and Evidence Register, cite prior art, invite public challenge, and bind the final wording to a release artifact.

## 2. Roles

### Maintainer

Owns repository health, release binding, review, and community conduct.

### Mechanism owner

Maintains one mechanism’s contract, fixtures, evidence, and known boundaries. Ownership does not grant unilateral authority to change cross-layer semantics.

### Benchmark steward

Freezes benchmark schemas, protects holdouts, reviews leakage channels, and publishes resource accounting. Ideally separated from treatment implementers for qualification runs.

### Failure Atlas curator

Ensures cases retain causal evidence, privacy discipline, counterexamples, and correct maturity labels.

### Translation maintainer

Protects canonical terminology and claim equivalence across languages.

### Community contributor

May propose changes, reproduce cases, provide baselines, and challenge claims.

## 3. RFC process

Use Discussions for early proposals. An RFC should include:

- problem and evidence;
- current behavior;
- alternatives, including no change and mature external technologies;
- proposed objects, states, events, and authority;
- consumers and integration path;
- safety/liveness trade-offs;
- failure and recovery;
- migration and rollback;
- public claim impact;
- implementation status plan.

The default outcome may be “not yet” or “use an external component.”

## 4. Release authority

A release must be bound to:

- exact source commit;
- immutable artifact manifest with SHA-256 hashes;
- schema and fixture versions;
- claim-register snapshot;
- generated documentation version;
- public demo output;
- known limitations.

The person assembling a release should not be the only person verifying the manifest and public claims.

## 5. Evolution of governance

A transition to a broader steering model should be considered when all are true:

- at least three independent recurring contributors;
- more than one mechanism owner;
- at least one external benchmark or fixture provider;
- a stable public API and release cadence;
- documented conflict-of-interest handling;
- maintainers have demonstrated sustained review responsibility.

Governance should follow real responsibility, not be invented for appearance.
