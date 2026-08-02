# Flowness local mechanism study — v0

Status: **private local code/test study, not a server-runtime registry**.

This is the first evidence-bearing study used to seed the formal Mechanism,
Unknown, and Drift registries. Every item below identifies code and test
coordinates, and most identifies a historical change to verify in the sealed
source snapshot. None establishes that the mechanism is currently deployed or
firing on an operator-provided runner; that requires the later redacted server Evidence Seal. Public
copy must retain that boundary.

## Cards

### MECH-EVT-001 — committed-visible batch ledger

- Why it exists: a process crash during a multi-event commit must not let a
  reader treat half the domain update as canonical.
- Path: Path-A domain records remain buffered until a `CommitAccepted` or
  `CommitRejected` sentinel; the sentinel makes the batch visible. Path-B is
  immediately visible and deliberately does not claim the same batch atomicity.
- Evidence: `l0/event_log/event_log.py:157-195,674-712,1900-1966`; event-log
  tests exercise hidden unsentinelled records and crash-tail truncation.
  Commit `3aaf56904` introduced the sentinel-backed physical batch path.
- Failure and recovery: an orphan Path-A tail is invisible and `recover()`
  truncates it. A Path-B record is individually durable, not a substitute for a
  batch.
- Open boundary: server crash/recovery frequency and active writer versions are
  unsealed.

### MECH-EVT-002 — cross-process sequence and provenance preservation

- Why it exists: daemon and session processes once derived the same next
  sequence from separate in-memory counters, producing duplicate sequence
  numbers and lossy replay.
- Path: a lock covers on-disk tail derivation, append, and fsync for both Path-A
  and Path-B; duplicate new sequences fail before write. Historical collisions
  are retained by event id and surfaced by audit rather than rewritten away.
- Evidence: `event_log.py:530-564,674-712,904-938,1040-1070,1689-1734`;
  multi-process tests assert 240 distinct gap-free records. `3aaf56904` records
  553 duplicate historical sequences; `d02f85b09` fixed audit/read handling.
- Failure and recovery: failed admission rejects a new collision; legacy
  contamination becomes an audit finding rather than silent deduplication.
- Open boundary: `segments.append_raw_event_line` is a restricted raw port with
  weaker safeguards and needs server-side producer/audit tracing.

### MECH-COMMIT-001 — envelope-to-gate commit or rejection

- Why it exists: an agent declaration, stale capsule, conflict, or unverified
  audit result must not become domain state by assertion.
- Path: the envelope is recorded, then integrity, provenance, birth, version,
  ownership, scope, freshness, claim and audit checks decide an accepted or
  rejected sentinel. Pending audit remains pending, not implicitly accepted.
- Evidence: `l0/commit_gate/gate.py:489-684,1258-1575,2092-2150`; integration
  tests cover freshness TOCTOU, audit pass/fail, and abandoned-envelope recovery.
  `0a0b90b44` narrowed a real abandoned-scan CPU failure without removing its
  recovery semantics.
- Failure and recovery: an abandoned envelope produces a linked rejection and
  lock release; drift routes to reassembly/rebase; audit failure rejects.
- Open boundary: checks can be `not_applicable`; do not say every production
  commit always exercises every check.

### MECH-PROJ-001 — replayable projections, watermarks, surgical repair

- Why it exists: downstream readers need queryable state without silently using
  stale or torn projection files, while a repair must not regress healthy views.
- Path: committed events reduce into a projection, state is flushed before its
  cursor/meta advances, and readers choose `allow_stale`, `fresh_required`, or
  `watermark_at_least`. State/meta hashes select one-projection recompute before
  a last-resort full rebuild.
- Evidence: `l0/projection/projection.py:245-330,756-953`; consistency,
  cross-projection, tamper/rebuild, and batch-coalescing tests. `fdbf00034`
  records a healthy `commit_history` regression from all-projection rebuild;
  `b9324ec4` added fail-closed watermark reads.
- Failure and recovery: unflushed work replays from the prior cursor; torn
  state/meta is detected and surgically rebuilt; insufficient watermark refuses
  to answer.
- Open boundary: actual reducer coverage and active projection use need server
  watermark/event evidence.

### M-ORCH-READYSET-EVENT-FANOUT — event-sourced ready-set fan-out

- Why it exists: a single-plan dispatch path can miss parallel-ready nodes or
  unlock dependents on a merely nominal completion.
- Path: `PlanFreezed`, successful `TaskRunCompleted`, and real
  `TaskNodeClosed` trigger task-graph recomputation and per-task dispatch
  decisions for execution or review.
- Evidence: `l2/orchestrator.py:702-1240` and dispatch/polling tests;
  `88a1bdc50` documents double-drive TOCTOU remediation.
- Failure and recovery: non-success does not unlock dependents; malformed
  stub-rewrap closure is rejected; existing output backlogs are skipped.
- Public boundary: daemon naming contains placeholder language, so this is not
  yet evidence of a generic production graph scheduler.

### M-ORCH-SINGLE-EXEC-CLAIM-FENCING — spawn claim and anti-twin fencing

- Why it exists: manual and automatic dispatch, or a slow session mistaken for a
  dead one, can spend twice and write conflicting results.
- Path: live-session evidence, owner gate, isolated worktree preparation,
  atomic claim, dispatch-stamp double check, fence renewal, and durable session
  events precede a child start.
- Evidence: `l2/orchestrator.py:1558+` and `9477+`; runtime-sentinel tests;
  `77a3e85fe` records stale-lock false death causing a twin dispatch.
- Failure and recovery: failed claim/preparation does not stamp success; only a
  vitality `DEAD` result permits reaping and redispatch.
- Public boundary: kind-level single-flight is currently an empty effective set;
  the defensible claim is per-key anti-double-drive, not universal serialization.

### M-ORCH-WATERMARK-BACKLOG-DECOUPLING — capacity-safe watermark advance

- Why it exists: holding a watermark at capacity causes the same tail to be
  rescanned, starving forward chains and burning CPU.
- Path: deferred decisions receive a backlog marker while the watermark still
  advances; markers rebuild work later through the same governor rather than
  bypassing limits.
- Evidence: `orchestrator.py:1385+`, `10820-11685`; `c684684bd` records a
  historical 96% CPU/cap saturation incident.
- Failure and recovery: markers survive capacity cutoffs and clear after dispatch
  or already-existing output; composite stamps keep one trigger's fan-outs apart.
- Public boundary: this is crash-safe/idempotent replay, not an exactly-once
  transaction across all cooperating files.

### M-REFLOW-OUT-OF-BAND-RECONCILIATION — stranded-worktree recovery

- Why it exists: a fast watermark window can permanently miss a successful task
  whose worktree was never promoted.
- Path: a full-ledger reconciliation builds promoted sets and emits a recovery
  finding for agent-first repair; the SLA sentinel separately detects old
  stranding and never directly promotes main.
- Evidence: `l2/reflow_reconcile.py:233+`, `reflow_sentinel.py:90+,385+`,
  reflow integration tests; `0feea5bb1` established the watermark-independent
  route and `d2b787917` fixed storms/blindspots/error handling.
- Failure and recovery: in-flight markers deduplicate; stale unresolved markers
  backstop re-emission; mint caps route to escalation.
- Public boundary: inventory subprocess failure can return an empty set and the
  sentinel is default-off; server timer/liveness evidence is mandatory.

### M-SESSION-LIVENESS-AND-DEADLETTER — conservative recovery and terminalization

- Why it exists: silence is not death, while unrouteable or repeatedly failing
  work must not hang forever or respawn without limit.
- Path: typed heartbeat plus PID evidence reap only stale locks; vitality permits
  harvest only for `DEAD`. Dead letters transition
  `enqueued → under_triage → redispatched|retired|escalated_to_owner`, with
  idempotency, re-entry cap, and TTL retirement.
- Evidence: `l1/session_lock.py:225+`, `l2/orchestrator.py:6283+`,
  `dead_letter_inbox.py:352+`, and unit/integration tests.
- Failure and recovery: `PARKED`, `STUCK`, and `UNKNOWN` are retained; revival
  is budgeted/marker-protected rather than an unconstrained respawn.
- Public boundary: automatic `revive` is dormant by default; it is not a current
  autonomous-recovery claim.

### MECH-REVIEW-001 — event-folded review finding lifecycle

- Why it exists: a completed-looking session once could hide a co-reviewer fail
  or unresolved major finding.
- Path: creation requires target, falsification, value, and closure contract;
  independent verification reaches a three-state result; verdict folds findings
  by sequence and only resolves/re-reviews may clear major or critical failures.
- Evidence: `l0/projection/review_verdict.py`, finding payload schemas, review
  verdict gate and related unit tests; `6dd05b16b` and `bbc57c244` are evolution anchors.
- Failure and recovery: absent/multi-task review session fails closed; unresolved
  verified findings keep the verdict failed.
- Open boundary: server multi-session and resolution chains are not sealed.

### MECH-CLOSURE-001 — contract-side closure recomputation

- Why it exists: a fixer can otherwise self-report a green result while leaving
  residuals, omitted scope, or unrelated work.
- Path: finding creation records criteria/ripple/residual contract; resolution
  recomputes grep, test, diff, and ledger evidence on the contract side, with
  one-to-one consumption and explicit `not_recomputable` downgrade.
- Evidence: `l1/closure_verification.py:843+`, finding-resolve CLI, and 153 unit
  tests; `d74134818`, `7585511d`, `f64c4d2cc` explain contract amendment and
  regex/soft-close failure fixes.
- Failure and recovery: residual/forged/unrecomputable conditions block strong
  closure; an amended closure contract supplies a bounded repair path.
- Drift: `test_run035_finding_extended` has a local git-artifact expectation
  mismatch (2 fail/34 pass in its narrowed integration run). Do not claim the
  complete CLI E2E is green until retested in a real git worktree.

### MECH-CAPSULE-001 — provenance-bound capsule context

- Why it exists: a worker needs context scoped to a reproducible projection and
  session rather than an untraceable conversational summary.
- Path: assembly takes a projection/event cutoff, expands a concept neighborhood,
  records input/content/section hashes and source event references, and ties the
  capsule to an owning session.
- Evidence: `l0/capsule/pipeline.py`, capsule payload schema and unit tests;
  `ec700a8e2`, `993cf71c9`, `a939b9d07` are evolution anchors.
- Failure and recovery: size caps truncate with provenance; further overflow or
  missing dependency aborts and emits an assembly/injection failure event.
- Open boundary: need a server capsule, downstream prompt consumer, and session
  attribution chain.

### MECH-PROV-001 — forward provenance anchor

- Why it exists: a frozen `FindingCreated` record cannot be backfilled with a
  later evidence-file relationship, leaving an audit trail without a stable
  reverse lookup.
- Path: a higher-sequence `RunStarted` anchor carries both finding and file in
  its subjects; subject indexing then lets a consumer walk from either side
  without rewriting the original finding.
- Evidence: `l0/event_log/provenance_anchor.py:1-139`,
  `schemas/event_intent.py:26-91`, `schemas/event_record.py:31-84`, and the
  temporary-ledger integration test `test_prov_anchor.py:151-208`.
  `d5e4c5aa6` records why an append-only forward anchor replaced attempted
  mutation of a frozen record.
- Failure and recovery: an empty anchor is rejected at construction; there is
  intentionally no “repair old event” path, only a new forward anchor.
- Public boundary: the helper is proven on an isolated code path, not yet as a
  demonstrated continuously-running production provenance system.

### MECH-VERIFY-001 — independent verification fork

- Why it exists: a producer should not be the sole judge of a finding or closure.
- Path: finding creation can launch a separate falsifier; failed/timeout/parse
  outcomes fail closed, and verify-step can override a weak closed result as
  `fix_insufficient`.
- Evidence: `l1/verification_fork.py:1317+`, CLI paths, run039 tests, and
  `568d3f057`, `1f9662f2f`, `2fdf34d60`.
- Failure and recovery: default gate-family failures stop admission; resume
  nudge and bounded fork concurrency are explicit paths.
- Public boundary: read-only tool policy includes Bash; physical isolation must
  be demonstrated by the actual server sandbox, not a deny-list claim.

### MECH-OWNER-001 — owner-answer reflow

- Why it exists: recognizing an owner answer is not the same as returning it to
  the parked session that needs it.
- Path: only a `PARKED_RESUMABLE` short-id session with transcript delivery
  confirmation becomes applied; dead sessions become stranded and other cases
  retry in a bounded unresolved state.
- Evidence: `l2/escalation_reflow.py`, owner/session interaction tests (45
  local unit/integration checks), and `2880d73c8` plus R08 history.
- Failure and recovery: silent death, failed PTY delivery, and wrong vitality
  classification are distinct terminal/retry states.
- Public boundary: interaction calculation itself is pure; live PTY delivery,
  transcript turn, and canonical answer events need server proof. There is no
  single executable “handoff runtime”; handoff remains distributed across
  capsule provenance, envelopes, and escalation reflow.

## Required server evidence before promotion

For every card: private source commit identity; relevant canonical event segment
or trace; projection/watermark relation where applicable; deployed unit/process
identity for daemon claims; a failure/recovery or explicit no-occurrence result.
These records will either promote a local implementation card, preserve it as
experimental/dormant, or create an Unknown/Drift record. They must never be
silently converted into marketing prose.
