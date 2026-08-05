# Repository Guide

This guide explains how a new contributor should read the Flowness repository without mistaking every historical artifact for the current public contract.

## 1. Recommended reading order

1. `README.md` — current project identity, runnable proof, and claim boundaries.
2. `docs/concepts/FLOW_ENGINEERING.md` — category definition.
3. `docs/architecture/README.md` — audience-specific architecture views.
4. `docs/benchmarks/CLAIMS_AND_EVIDENCE_REGISTER.md` — what is actually proven.
5. `docs/demos/ASSURANCE_KERNEL_DEMO.md` — current public demo.
6. `harness/src/towow/l0/` — truth, projection, capsule, envelope, gate.
7. `harness/src/towow/l2/` — dispatch, reconciliation, liveness, reflow.
8. `harness/src/towow/l1/` and `l3/` — semantics, governance, human control.
9. Historical design and dogfood materials — context, not automatically current contract.

## 2. High-level tree

```text
.
├── README.md                         project entry and claim boundary
├── README.zh-CN.md                  Chinese entry
├── docs/                            concepts, architecture, evidence, cases
├── harness/src/towow/               broader inspectable and dogfood runtime
│   ├── l0/                           Flow kernel
│   ├── l1/                           semantic and governance mechanisms
│   ├── l2/                           runtime, liveness, reflow, maintenance
│   ├── l3/                           human control surface
│   ├── schemas/                      typed contracts
│   ├── skills/                       model-facing operating procedures
│   ├── shell/                        command and runtime adapters
│   └── glue/                         integration paths
├── oss/flowness-oss-harness/         public deterministic Open Alpha
├── public-core/flowness-ledger-core/ reusable ledger-related public core
└── .github/                          contribution and community surfaces
```

## 3. Authority order

When sources disagree, do not infer authority from file age or confidence of prose. Use this order:

1. exact release artifact and immutable manifest;
2. executable code and tests in the bound source commit;
3. public schemas and conformance contracts;
4. current README and claim register;
5. current architecture / mechanism docs;
6. dogfood evidence with explicit provenance;
7. historical design material;
8. informal discussion.

A newer copy of an old document is not automatically fresher than an older executable contract.

## 4. Current public products inside one repository

The repository contains at least two different public surfaces and should name them clearly.

### Assurance Kernel / OSS Harness

- deterministic;
- no model account required;
- execution, isolated review, targeted rework, fresh acceptance;
- suitable for reproducible public verification.

### Broader Flowness runtime

- larger inspectable mechanism set;
- includes truth, projection, context compilation, semantic objects, runtime maintenance, human control, and dogfood paths;
- public completeness varies by component;
- must use status labels.

Do not imply that the narrow demo is the full runtime. Do not hide the narrow demo merely because it is narrower; it is the strongest reproducible public proof currently available.

## 5. How to document a module

Every load-bearing module should answer:

```text
Purpose
Authoritative inputs
Outputs / events
State owned
State not owned
Consumers
Idempotency
Ordering and concurrency assumptions
Failure modes
Recovery
Evidence
Security / authority boundary
Status label
Known unconnected paths
```

A model-oriented prompt or Skill should additionally state:

- what behavior is advisory;
- what is enforced mechanically;
- what requires human authority;
- what evidence binds its output;
- which version of context it consumed.

## 6. How to document an event

```yaml
event_type: FindingCreated
schema_version: v1
producer_authority: validator | runtime | human
required_fields: []
idempotency_key: ...
exact_subject_refs: []
consumers: []
expectation_created: false
projection_effects: []
invalidates: []
retention: immutable
security_classification: ...
```

Every event needs a consumer or an explicit archival-only declaration. An event that is expected to advance Work but has no consumer is a continuity defect.

## 7. How to document a projection

Specify:

- source event classes;
- fold function;
- watermark semantics;
- freshness contract;
- rebuild procedure;
- stale-read behavior;
- divergence detection;
- whether the view is authoritative or derived;
- exact consumers.

## 8. How to document a gate

Specify:

- fact the gate is supposed to judge;
- source of that fact;
- physical, model, or human tier;
- pass, fail, and abstain behavior;
- false-pass / false-fail consequence;
- bypass authority and audit trail;
- version binding;
- whether the check runs on every relevant envelope;
- counterexample fixture.

Avoid gates that read a convenient field instead of the fact they claim to protect.

## 9. Public naming discipline

Use these canonical terms:

- `Work` — persistent semantic work object;
- `Execution` — one attempt;
- `Flow` — Work history and state evolution;
- `GraphSnapshot` — versioned structural projection;
- `ContextCapsule` — bounded current-world view;
- `Finding` — persistent evidence-bound problem;
- `Reflow` — invalidation plus partial recompilation;
- `Activation` — organic target-environment use;
- `Acceptance` — binding owner judgment.

## 10. First contributions we should invite

Good first contribution classes:

- replayable failure fixtures;
- projection and gate property tests;
- documentation of existing public contracts;
- adapters that keep truth and authority boundaries intact;
- benchmark providers that implement the frozen interface;
- explanation views such as `why-blocked`;
- translations with terminology QA;
- diagram accessibility and alt text;
- counterexamples that narrow claims.

Avoid labeling major orchestration or schema changes as “good first issue.”
