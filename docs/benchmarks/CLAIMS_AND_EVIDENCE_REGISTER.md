# Claims and Evidence Register

**Purpose:** keep product language, public artifacts, dogfood experience, design intent, and research hypotheses from being merged into one story.

Update this file at every release and bind it to the release manifest.

## Status vocabulary

| Status | Meaning |
|---|---|
| `[RUNNABLE]` | A clean public clone can reproduce the behavior |
| `[INSPECTABLE]` | Public code, tests, schemas, or contracts exist, but no complete public path |
| `[DOGFOOD]` | Used or observed in sustained private work; public evidence may be sanitized |
| `[DESIGNED]` | Specified or implemented as an isolated component, not connected end to end |
| `[OPEN QUESTION]` | Research target or hypothesis |

## Current claim register

| ID | Claim | Status | Current evidence | What would upgrade it |
|---|---|---|---|---|
| C-001 | The public Open Alpha can reproduce execution → independent review → targeted rework → fresh acceptance without a model account. | `[RUNNABLE]` | `open-alpha-demo`, inspector, fixture output | independent clean-clone reproduction in release CI and hash-bound artifact |
| C-002 | The broader repository contains an append-only event truth layer, projections, context-capsule logic, envelopes, gates, obligations, and selected runtime mechanisms. | `[INSPECTABLE]` | public source and tests under `harness/src/towow` | per-module public conformance matrix and bound release docs |
| C-003 | Work can outlive active Agents in the public product surface. | `[DESIGNED]` | WorkView and hero-demo specifications | runnable `agents:none / flow:alive` demo and public CLI |
| C-004 | Graphs can be recompiled from current Work state after a Finding. | `[DESIGNED] / [DOGFOOD]` | projections, dispatch, invalidation and dogfood paths | public fixture where a non-prewritten Finding creates Graph v2 |
| C-005 | Context Capsules are compiled from one committed fact cutoff. | `[INSPECTABLE]` | capsule pipeline and event/projection code | public conformance test and replay artifact |
| C-006 | Reconciliation can detect and repair selected desired/observed state gaps. | `[INSPECTABLE] / [DOGFOOD]` | reconcile code and dogfood incidents | public failure fixture with mechanism-off/on comparison |
| C-007 | Flowness distinguishes built, integrated, activated, and accepted states. | `[INSPECTABLE]` | activation and closure mechanisms; current docs | public organic activation demo bound to real consumer evidence |
| C-008 | A complete new public goal can traverse problem, design, engineering, plan, execution, validation, reality, and acceptance organically. | `[OPEN QUESTION]` | partial mechanisms and private runs | a new public repository task with full replayable artifact |
| C-009 | Flowness reduces failure, cost, or human load relative to strong mature baselines. | `[OPEN QUESTION]` | no frozen comparative FlowBench result | qualified independent benchmark with resource accounting |
| C-010 | Flow Engineering is a useful category beyond software engineering. | `[OPEN QUESTION]` | conceptual argument | successful domain transfer with preserved semantics and evidence |
| C-011 | Safety-critical covered paths do not depend on LLM-oracle correctness. | `[DESIGNED / FORMAL, CONDITIONAL]` | position paper / technical report; conditional completeness boundary | formal artifacts plus implementation conformance and empirical counterexamples |
| C-012 | Long-horizon interpretation drift is usefully modeled through distributed-consistency machinery. | `[POSITION]` | formal paper and architecture correspondence | independent critique, empirical DriftBench, alternative-model comparison |
| C-013 | Dogfood audit found dozens of structural defects across a large event history. | `[DOGFOOD, SELF-REPORTED]` | sanitized cases and internal audit records | release-bound anonymized dataset, methodology, and independent replication |
| C-014 | Flowness is the first Flow Engineering runtime. | **Not an approved claim** | prior term use exists; “first” unproven | do not use without exhaustive defensible prior-art evidence |
| C-015 | Flowness is a universal Harness that should replace existing frameworks. | **Not an approved claim** | conflicts with project philosophy | not a target |

## Claim-writing rules

1. Never let a rendered identity outrun the release artifact it refers to.
2. Bind exact claims to exact source commits and manifests.
3. Use “designed,” “inspectable,” and “dogfood” explicitly; do not translate them into “supports.”
4. A formal result about an abstract model is not automatically an implementation guarantee.
5. A private incident count is valuable evidence, but not an independent benchmark.
6. A demo proves only the channel it actually exercises.
7. When a stronger mature system solves a case more simply, say so and revise the Flowness claim.
