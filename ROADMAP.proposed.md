# Flowness Public Roadmap

This roadmap separates identity, visible runtime behavior, falsifiable evidence, and community compounding. Dates should be assigned only after owners and capacity are confirmed.

## R0 — Identity correction

**Goal:** make the public project say what the code is becoming without overstating current proof.

Deliverables:

- work-centered README in English and Chinese;
- Flow Engineering concept kit;
- multi-level architecture diagrams;
- renamed Assurance Kernel Demo;
- Claims and Evidence Register;
- repository and contribution guides;
- public status labels.

Exit criteria:

- a new reader can state what Flowness is in 30 seconds;
- the runnable demo is not confused with the whole runtime;
- no unsupported “first,” “complete,” or superiority claim remains.

## R1 — Work becomes visible

**Goal:** make Work the public subject of the runtime.

Deliverables:

- `WorkView` projection;
- `flowness work show/next/explain/graph/history/evidence` commands;
- explicit Work / Execution / Flow / GraphSnapshot terminology;
- active/waiting/blocked/stale/effect/activation/acceptance states;
- clean restart and replay tests.

Exit criteria:

- `agents: none / flow: alive` is a real public state;
- Work survives process and provider replacement;
- CLI state is derived from authoritative events, not a parallel store.

## R2 — Work Outlives Agents hero demo

**Goal:** demonstrate the category in under 90 seconds and reproduce it in under 5 minutes.

Deliverables:

- deterministic fixture;
- agent-loss injection;
- fresh execution compilation;
- Finding-driven Graph v1 → v2;
- built/integrated/activated/accepted separation;
- prepared Owner Inbox decision;
- source and artifact manifest binding;
- mechanism-off mutation switches.

Exit criteria:

- clean clone reproduces the run;
- independent inspection passes;
- README switches to post-demo version;
- claim C-003 becomes `[RUNNABLE]`.

## R3 — Failure Atlas laboratory

**Goal:** turn dogfood learning into reusable public experiments.

Deliverables:

- first 10 replayable cases;
- seven-family taxonomy;
- mechanism-off/on comparisons;
- counterexamples and costs;
- community failure-fixture template;
- monthly Flow Clinic.

Exit criteria:

- at least three cases reproduced by an external contributor;
- at least one Flowness mechanism is narrowed or replaced after a counterexample.

## R4 — Public organic SWE Flow

**Goal:** prove a new non-trivial task from intent through accepted reality.

Deliverables:

- frozen public target repository;
- Problem, Design, Engineering, Consensus, Plan, Execution, Finding, Reflow, activation, acceptance artifacts;
- at least one non-prewritten graph change;
- full event/evidence replay;
- resource and human-load accounting.

Exit criteria:

- an external reviewer can reconstruct why each transition happened;
- no evaluator-private expected route is exposed;
- final artifacts bind to source and release hashes;
- claim C-008 becomes `[RUNNABLE]` for the bounded profile.

## R5 — FlowBench qualification

**Goal:** compare specific Flow properties without leakage or unfair treatment definitions.

Deliverables:

- frozen provider interface;
- 8–12 development fixtures and fresh holdout;
- strong mature baselines;
- resource budgets;
- qualification attacks;
- vector metrics;
- independent provider implementation.

Exit criteria:

- leakage attacks fail;
- results are reproducible;
- failures identify exact channels;
- no single aggregate winner claim hides trade-offs.

## R6 — Cognitive Exoskeleton

**Goal:** demonstrate that judgment compounds across Work.

Deliverables:

- JudgmentCase capture and supersede;
- direct/indirect/structural retrieval with counterexamples;
- candidate rule/skill/validator lifecycle;
- shadow evaluation and rollback;
- human promotion gate;
- cross-project portability.

Exit criteria:

- one repeated failure class is reduced without unacceptable false positives;
- a new model/provider can reuse the same human-owned judgment assets.

## Non-goals

- claiming universal domain support before software-engineering proof;
- replacing every mature workflow, graph, CI, or policy technology;
- maximizing Agent count;
- removing humans from authority and acceptance;
- making every task pass the full high-assurance lifecycle;
- hiding complexity or failure behind a single green score.
