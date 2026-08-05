# Module Documentation Template

Copy this template into load-bearing module documentation or the top-level module docstring.

```text
Name
====

Responsibility
--------------
What single responsibility does this module own?

Why it exists
-------------
Which observed failure or architectural obligation requires it?

Authoritative inputs
--------------------
List exact events, projections, files, external systems, and freshness assumptions.

Outputs and consumers
---------------------
For every output, name the consumer. Mark archival-only outputs explicitly.

State ownership
---------------
What is this module authoritative for? What nearby state does it not own?

Lifecycle
---------
States and valid transitions.

Ordering, concurrency, and idempotency
--------------------------------------
Sequence assumptions, locks, deduplication, replay behavior, and crash points.

Evidence and version binding
----------------------------
How claims bind to exact objects, cutoffs, commits, and hashes.

Authority
---------
Physical, model, and human roles. Who may bypass and how is that recorded?

Failure modes
-------------
Known safety, liveness, integrity, adaptation, commitment, closure, and learning failures.

Recovery and Reflow
-------------------
Retry, repair, replan, re-engineer, redesign, or re-interview behavior.

Observability
-------------
Metrics, logs, traces, expected signals, timeout, and dead-letter path.

Security and privacy
--------------------
Read/write boundaries, secret handling, taint propagation, and exposure risks.

Performance and cost
--------------------
Latency, token, storage, operator load, and known scaling limits.

Tests and fixtures
------------------
Unit, property, mutation, integration, replay, and counterexample fixtures.

Maturity
--------
[RUNNABLE] / [INSPECTABLE] / [DOGFOOD] / [DESIGNED] / [OPEN QUESTION]

Open questions
--------------
What remains unproven or deliberately unsupported?
```
