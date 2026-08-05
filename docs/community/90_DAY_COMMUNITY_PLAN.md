# 90-Day Open-Source Community Plan

The first 90 days should create a small, technically serious community around reproducible Flow failures and mechanisms. A large chat group with little shared work is not the goal.

## Operating cadence

- **Weekly:** one Failure Atlas case or mechanism note.
- **Biweekly:** public Flow Clinic in GitHub Discussions or live call with written recap.
- **Monthly:** release or evidence snapshot.
- **Quarterly:** roadmap and claims review.

## Days 0–14 — Make the project inhabitable

Ship:

- README, status labels, architecture, concept kit;
- contribution and security files;
- Discussion categories: Announcements, Q&A, Flow Clinic, RFC, Failure Atlas, Show and Tell;
- issue forms;
- current demo smoke test in CI;
- first three “help wanted” tasks with bounded acceptance.

Community actions:

- invite 10 reviewers individually;
- ask them to reproduce one command and explain one concept;
- open a “What would falsify Flow Engineering?” Discussion;
- publish a glossary and accept terminology challenges.

Success signal:

- at least five people outside the core can run the demo;
- at least three articulate the project correctly without coaching.

## Days 15–30 — Turn criticism into assets

Publish:

- Failure Atlas cases 1–3;
- “Graph is a projection, not Flow” deep dive;
- Human Constitution note;
- one mechanism-off/on fixture;
- public list of claims narrowed after review.

Community actions:

- first Flow Clinic;
- label counterexamples as first-class contributions;
- invite maintainers of adjacent projects to correct comparisons;
- create a “simpler existing technology” issue label.

Success signal:

- one external counterexample changes a document, fixture, or claim;
- one contributor adds evidence rather than only prose.

## Days 31–60 — Build the Flow-first surface

Focus effort on:

- WorkView projection;
- CLI status and explainability;
- hero demo implementation;
- release-manifest binding;
- visual trace.

Community actions:

- publish implementation design before code is frozen;
- split tasks by projection, CLI, fixture, visualizer, and release QA;
- pair one contributor with one mechanism owner per task;
- run an external clean-clone qualification.

Success signal:

- an external contributor owns a bounded part;
- `agents:none / flow:alive` works outside the maintainer machine.

## Days 61–90 — Establish a research loop

Publish:

- FlowBench v0.1 interface;
- baseline provider call;
- first holdout-threat review;
- Failure Atlas cases 4–8;
- public organic E2E task selection and freeze packet.

Community actions:

- recruit one independent benchmark steward;
- host a “red-team the benchmark” session;
- invite a provider implementation not written by the core maintainer;
- publish resource accounting and failures, not only green results.

Success signal:

- at least one independent provider or fixture;
- a documented leakage attack caught before comparative runs;
- a second recurring maintainer candidate.

## Discussion categories

### Announcements

Maintainer-authored releases and claim updates.

### Q&A

Concrete usage and architecture questions; mark accepted answers.

### Flow Clinic

Real or synthetic cases of stopped, drifting, stale, uncommitted, or falsely closed Work.

### Failure Atlas

Case proposals, replications, and counterexamples.

### RFC

Public contracts, schemas, mechanisms, and governance changes.

### Show and Tell

Personal Harnesses, adapters, visualizers, and domain experiments.

## Maintainer response standard

- acknowledge within 72 hours when feasible;
- distinguish support, bug, RFC, and research question;
- do not promise timelines without an owner;
- convert repeat questions into docs;
- close stale issues with a reason and reopen path;
- thank people for disproving an overbroad claim;
- never use AI-generated volume to simulate maintainer attention.

## Contributor ladder

```text
reader
→ demo reproducer
→ case reporter
→ fixture contributor
→ mechanism contributor
→ mechanism owner
→ maintainer / benchmark steward
```

Promotion is based on sustained responsibility and review quality, not raw commit count.

## Community health dashboard

Track monthly:

- unique demo reproductions;
- time to first maintainer response;
- open/closed Failure Atlas cases;
- external fixtures and counterexamples;
- number of contributors returning for a second month;
- percentage of issues with exact release/commit info;
- claim changes caused by community evidence;
- maintainer workload and burnout signals.
