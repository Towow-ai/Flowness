# Flowness Ledger Core candidate — evidence-bounded casebook

Status: **public Open Alpha evidence-bounded casebook**. These are repeatable local code paths,
not customer stories, production incidents, benchmarks, external adoption, or
proof that the larger Flowness runtime has this behavior. Each case links to
the exact implementation and test/demonstration that currently supports it.

## How to read a case

The **observable evidence** is deliberately narrow: it says what a local
reader or verifier can inspect after exercising this candidate. **Cannot
prove** is part of the case, not a disclaimer that can be removed at release
time. A changed source snapshot, test, demo artifact, or candidate boundary
requires the case to be re-reviewed.

## Case 1 — an accepted change becomes visible as one committed result

**Status:** `experimental_open_alpha_local`

### Problem

A consumer must not act on a partial change while its proposal is still under
review. The underlying problem is a draft/decision distinction, rather than a
failure to persist bytes. The candidate contract is that durable proposed
records become visible to the committed reader only after an immutable
`accepted` decision.

### Flow

1. A caller starts `P-accepted` and appends `change.requested` plus
   `artifact.checked`.
2. `Ledger.read("committed")` returns an empty list while the proposal is
   pending.
3. The caller records the terminal `accepted` decision.
4. The same reader now yields the two proposed records, in order.

### Observable evidence

- The executable demonstration performs this exact path at
  [`demo.py:54-62`](../src/flowness_ledger_core/demo.py#L54-L62), and writes
  its observed types plus invariant results to a self-hashed
  `demo-run.json` at
  [`demo.py:104-141`](../src/flowness_ledger_core/demo.py#L104-L141).
- The committed reader computes visibility from terminal accepted decisions at
  [`ledger.py:222-232`](../src/flowness_ledger_core/ledger.py#L222-L232).
- The unit test checks pending invisibility and then the two accepted types at
  [`test_ledger.py:12-21`](../tests/test_ledger.py#L12-L21).
- The independently callable demo verifier validates artifact hashes,
  recovery receipt, and negative invariants at
  [`demo.py:145-193`](../src/flowness_ledger_core/demo.py#L145-L193).

### Failure and recovery boundary

The visible-state failure guarded here is premature consumption: a pending
proposal is not emitted by the committed view. This case has no compensating
business rollback because it does not execute a business action; it prevents
that action from being justified by the committed reader in the first place.
Malformed completed entries, hash breaks, duplicate IDs, and invalid
transitions are rejected by ledger loading/indexing rather than repaired
silently; see the failure contract in the
[technical report](LEDGER_CANDIDATE_TECHNICAL_REPORT.md#d3--mechanism-and-failure-behavior).

### Cannot prove

It cannot prove real agent authorization, a workflow's semantic correctness,
distributed atomicity, exactly-once delivery, a consumer's actual behavior,
or production durability. It is only evidence for the local candidate's
committed-view rule.

## Case 2 — a rejected proposal and later conflicting decision do not rewrite history

**Status:** `experimental_open_alpha_local`

### Problem

If a proposal is rejected for insufficient evidence, a later caller must not
retroactively turn that terminal decision into acceptance. Otherwise the audit
trail and the committed reader would disagree about what was decided.

### Flow

1. A caller starts `P-rejected`, appends `change.rejected`, then records a
   terminal `rejected` decision.
2. The committed reader retains the earlier accepted case but excludes the
   rejected proposed record.
3. A later `accepted` decision with different immutable input is attempted.
4. The ledger raises `LedgerError` for a conflicting immutable decision;
   existing decision bytes remain unchanged.

### Observable evidence

- The mixed success/negative demonstration takes the rejected and conflict
  branches at [`demo.py:64-74`](../src/flowness_ledger_core/demo.py#L64-L74),
  records the error, and asserts both rejection-related invariants at
  [`demo.py:116-129`](../src/flowness_ledger_core/demo.py#L116-L129).
- The terminal-decision guard returns an identical repeated decision but
  rejects a changed outcome or payload at
  [`ledger.py:207-220`](../src/flowness_ledger_core/ledger.py#L207-L220).
- The focused unit test covers the idempotent rejection, conflicting acceptance
  failure, and empty committed result at
  [`test_ledger.py:24-33`](../tests/test_ledger.py#L24-L33).

### Failure and recovery boundary

This is a fail-closed conflict path, not an undo mechanism. The candidate
preserves the original terminal decision and surfaces the conflict to its
caller. A caller that needs a new legitimate decision must create a new
proposal; this case does not define approval policy, escalation, or how a
human resolves disagreement.

### Cannot prove

It cannot prove that a caller will react safely to `LedgerError`, that all
external systems retain the same history, that a reviewer was authorized, or
that a rejected proposal was substantively correct. It does not model quorum,
delegation, or cross-process review governance.

## Case 3 — an interrupted final write blocks reads until bounded tail recovery

**Status:** `experimental_open_alpha_local`

### Problem

A process can stop after appending an unterminated final JSONL fragment. A
reader must not silently treat this as a normal complete stream, nor truncate
earlier valid history while recovering it.

### Flow

1. A pending proposal has two complete ledger records.
2. A deliberately unterminated final JSONL fragment is appended.
3. A committed read refuses with an incomplete-tail error.
4. `recover(persist_report=True)` truncates only the final fragment and writes
   a self-hashed recovery receipt bound to the resulting ledger prefix.
5. A verifier reopens the ledger and checks the persisted receipt against that
   prefix.

### Observable evidence

- The demonstration injects the unterminated fragment, observes the refused
  read, persists recovery, and records its receipt at
  [`demo.py:76-114`](../src/flowness_ledger_core/demo.py#L76-L114).
- Loading identifies only a non-newline final tail as recoverable at
  [`ledger.py:91-119`](../src/flowness_ledger_core/ledger.py#L91-L119); all
  complete lines continue through immutable-record validation.
- The focused test captures the original bytes, verifies the post-recovery
  stream matches them exactly, and checks the receipt/hash at
  [`test_ledger.py:36-56`](../tests/test_ledger.py#L36-L56).
- A tampered recovery state summary is rejected by a separate test at
  [`test_ledger.py:59-69`](../tests/test_ledger.py#L59-L69).

### Failure and recovery boundary

The recovery operation is deliberately bounded to an incomplete *final* line.
It does not repair malformed completed lines, a broken hash chain, duplicate
IDs, a modified receipt, or a semantic error in a proposed payload. Those
conditions remain failures. The technical report explains the distinction and
the receipt's prefix scope at
[D3](LEDGER_CANDIDATE_TECHNICAL_REPORT.md#d3--mechanism-and-failure-behavior).

### Cannot prove

It cannot prove crash consistency across filesystems, NFS behavior, concurrent
distributed writers, a complete incident response process, recovery from
arbitrary corruption, or production operational reliability. It is evidence
only for this POSIX-local incomplete-final-tail path.

## Cross-case boundary

All three cases use the local package's intentional Open Alpha demo boundary
`public_open_alpha_local_demo_not_production`; the demo writes it into its
manifest at [`demo.py:92-95`](../src/flowness_ledger_core/demo.py#L92-L95).
The broader limits—no production evaluation, external-adoption evidence, or
wider Flowness runtime compatibility claim—remain stated in the
[technical report](LEDGER_CANDIDATE_TECHNICAL_REPORT.md#flowness-ledger-core-candidate--technical-report).
