# Contributor Map: Current Code to Flow Architecture

This document is a reader-facing map of the current `harness/src/towow` codebase. It is not a claim that every module is fully connected in the public runtime.

## L0 — Flow Kernel

**Purpose:** durable truth, bounded reads, guarded writes, and recoverable projections.

Representative areas:

- `l0/event_log/` — append-only event truth, transaction visibility, replay, schema handling;
- `l0/projection/` — deterministic views, watermarks, freshness and rebuild;
- `l0/capsule/` — current-world context compilation from a committed cutoff;
- `l0/obligations/` — activation and lifecycle of mandatory conditions;
- `l0/envelope/` — bounded proposed changes and claims;
- `l0/commit_gate/` — physical/model/environmental checks before truth mutation;
- `l0/snapshot/` — recovery and consolidation support.

Contributor questions:

- Is the source of truth explicit?
- Can state be replayed deterministically?
- Is every write bound to exact sources and versions?
- Is stale state detected or silently accepted?
- Is a safety-critical check enforced outside the model tier?

## L1 — Semantic and Governance Mechanisms

**Purpose:** express what work means, what must remain true, what evidence counts, and what human judgment controls.

Representative areas:

- goals and requirements;
- concept and consensus objects;
- findings and closure;
- judgment cases and retrieval;
- activation evidence;
- consumer coverage;
- owner decisions and authority-related semantics;
- engineering decisions and acceptance.

Contributor questions:

- Does this object have a lifecycle and exact identity?
- Can it be challenged, superseded, or reopened?
- What consumes it?
- What evidence is authoritative?
- Does the model merely recommend, or does it improperly become authority?

## L2 — Flow Runtime and Maintenance

**Purpose:** keep work moving, detect silent gaps, recover, and change future execution when assumptions fail.

Representative areas:

- `execution_dispatch.py` — ready-set and bounded dispatch;
- `reconcile_loop.py` — desired/observed state repair;
- liveness and expectation tracking;
- dead-letter handling;
- Finding routing;
- reflow routes;
- `invalidation_cascade.py` — forward affected-slice computation;
- session lifecycle and recovery;
- periodic maintenance / patrol mechanisms.

Contributor questions:

- Who consumes every produced event?
- What expectation exists after dispatch?
- What wakes waiting work?
- Can an active session die without losing the Work?
- Is a failure retryable, or does a higher layer need to reopen?
- Can impact be limited to the affected slice?

## L3 — Human Control Surface

**Purpose:** use human attention where value, authority, or irreversible effects make it load-bearing.

Representative areas:

- owner inbox;
- prepared decision views;
- health and signal surfaces;
- explain-why-blocked and work status views;
- dashboards and operator actions.

Contributor questions:

- Is the human shown authoritative context rather than raw transcript volume?
- Is the question actually blocking?
- Are options and consequences explicit?
- Does the human decision bind future system behavior?
- Can the owner inspect Work without entering an Agent session?

## External world

The runtime is incomplete without explicit adapters to reality:

- repositories and worktrees;
- CI and tests;
- package and release systems;
- deployment environments;
- production consumers;
- monitoring and readback;
- human organizations and acceptance.

Do not model every external signal as equivalent. A unit test, a demo invocation, a deployment event, an organic production call, and an owner acceptance are different evidence classes.

## A contribution path

A new mechanism should be introduced through this sequence:

1. **Failure fixture:** show the structural failure with the mechanism absent.
2. **Contract:** define inputs, outputs, authority, evidence, idempotency, lifecycle, and failure behavior.
3. **Pure core where possible:** isolate deterministic logic from model or environment calls.
4. **Event and projection impact:** identify what becomes authoritative and how it is read.
5. **Consumer:** prove who consumes every emitted signal.
6. **Integration path:** connect it to the existing canonical route instead of adding a parallel control plane.
7. **Counterexample:** show where the mechanism should not fire.
8. **Cost:** measure latency, token, operator load, and maintenance burden.
9. **Status label:** mark it runnable, inspectable, dogfood, designed, or open question.

## What maintainers should reject

Reject changes that:

- add a new “success” field without identifying its authoritative fact;
- create an event with no consumer, expectation, or dead-letter path;
- trust a producer’s self-report where independent readback is available;
- introduce a second source of truth for the same state;
- attach evidence to a floating filename instead of an exact object/version;
- use a model-only gate for a safety-critical property that can be checked mechanically;
- add a fixed pipeline step where state-driven compilation is the better abstraction;
- claim generality without a public, bound artifact.
