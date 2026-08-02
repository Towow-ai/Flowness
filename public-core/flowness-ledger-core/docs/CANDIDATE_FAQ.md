# Flowness Ledger Core candidate FAQ

Status: **public Open Alpha; local, experimental, and non-production.**

## What problem does it address?

When a change first collects records and only later receives a decision, an
unfinished proposal can be mistaken for downstream truth. The Ledger candidate
keeps those phases separate: proposal records are durable, but the committed
reader exposes their complete group only after an explicit `accepted` decision.

Think of it as a file kept in an envelope until it receives an acceptance
stamp. A rejection remains in the audit history, but never enters the official
file.

## What can I verify now?

The candidate wheel's `flowness-ledger-demo --demo-dir <new-directory>` emits
`demo-run.json`. In one inspectable local run it shows pending invisibility,
grouped accepted visibility, rejected invisibility, conflicting-decision
refusal, and recovery that truncates only an interrupted final JSONL tail.

These are local candidate semantic evidence—not performance, distributed
consensus, production reliability, or real Agent-orchestration evidence.

## What is it not?

It is not a complete Flowness platform and does not run Agents or handle
credentials, task graphs, servers, worktrees, or customer data. It now has one
committed-type projection whose watermark covers the audit head and whose
stale reads refuse until rebuild. Its read-only review-verdict adapter requires
an immutable terminal decision. Linux aarch64 / CPython 3.12 is the intended
first independent clean-room coordinate, but the exact candidate still
requires a retained non-author receipt and fresh jury decision. This package
currently claims no independently reproduced compatibility coordinate.

## What does the Open Alpha label mean?

The label identifies the intended public scope and maturity, not completed
external acceptance. Before publication, a future exact release record must
bind the source/license boundary, sealed export, versioned artifact, negative
E2E, non-author clean-room receipt, fresh jury decision, and owner gate.
Running the local demo does not complete those requirements or promote this
slice to Beta or production.

## What happens if demo evidence changes?

The candidate Harness places demo evidence, mechanism, claim, explanatory
section, README and FAQ assets in its Content Graph. Changed evidence or a
changed mechanism returns dependent materials to `evidence_bound` for review
before they can progress to staged or higher states.
