<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flowness Ledger Core

Ledger Core is the smallest runnable Open Alpha slice of Flowness. It provides
an append-only local decision ledger with explicit visibility, terminal
decisions, projection freshness, and bounded crash-tail recovery.

## What it implements

- hash-linked local JSONL records with monotonic sequence numbers;
- proposal records that remain invisible to the committed reader until an
  explicit `accepted` decision;
- durable rejection and conflict rejection;
- committed-type projection with a hash-bound audit-head watermark;
- stale-read refusal and deterministic projection rebuild; and
- quarantine and deterministic recovery of an incomplete final JSONL tail.

The installed walkthrough demonstrates pending invisibility, atomic accepted
visibility, durable rejected invisibility, conflict failure, projection
freshness, and crash-tail recovery. Its verifier rejects modified manifests,
artifact hashes, recovery receipts, and ledger-prefix drift.

## Quickstart

Use the [Alpha quickstart](docs/ALPHA_QUICKSTART_CANDIDATE.md). It selects an
eligible interpreter, creates isolated paths, builds a wheel, runs the installed
`flowness-ledger-demo` command, and verifies the resulting artifacts.

The intended first independently accepted full-Flowness Alpha coordinate is
**Linux aarch64 with CPython 3.12**. Ledger Core itself declares Python 3.11 or
newer and a POSIX-local environment, but this mutable candidate contains no
non-author clean-room receipt for its exact bytes. Treat every coordinate as
unverified until the pre-release clean-room and jury gates are completed and a
future exact release record binds their evidence.

## Boundaries

Ledger Core is local and deliberately narrow. It does not implement agent
orchestration, distributed consensus, exactly-once delivery, secret handling,
a server, a worktree manager, or autonomous recovery. It has no production
reliability, durability-across-filesystems, performance, scale, security, or
external-adoption claim.

The technical report, measurement fixtures, and scenario pack are diagnostic
materials, not comparator benchmarks. Alpha APIs and on-disk formats may change
before Beta.

Code is Apache-2.0. Documentation is CC-BY-4.0 unless a more specific
file-level notice applies.
