# Flowness terminology six-packs — v0

Status: **local terminology candidate; every term below is `experimental`**.

This is a reader aid for the local mechanism study, not a new Flowness
vocabulary. It deliberately prefers established engineering language. A term
is included only when it makes a located mechanism easier to understand, and
each card names what it cannot prove. No card establishes a deployed service,
an adoption claim, an end-to-end guarantee, or a public API.

The evidence ceiling is the unsealed local code/test study and the mechanism
registry seed. The required server Evidence Seal can promote, retain, narrow,
or remove these cards; writers must use the lower of this status and any
applicable Drift finding.

## 1. Commit marker (batch visibility)

**Status:** `experimental` · **Mechanism:** `MECH-EVT-001`

**One-sentence definition.** A commit marker is a final record that makes a
previously written batch visible to readers only after the batch has reached a
decided outcome.

**Everyday analogy.** It is a restaurant pass: cooks may prepare several
dishes behind the counter, but the table should only see the order when the
pass says it is ready.

**Real failure it addresses.** A process crash during a multi-event update can
otherwise leave readers treating the first half of a domain change as if it
were the completed change. The studied Path-A keeps records hidden until a
`CommitAccepted` or `CommitRejected` sentinel; an orphan tail is recoverable.

**Mechanism-diagram cue.** Draw
`buffered records → accepted/rejected marker → reader-visible batch`, with a
side branch `crash before marker → invisible orphan → truncate/recover`.
Draw Path-B separately as `one durable record`, never as batch-atomic.

**Minimum demo.** In a disposable fixture, append two Path-A domain records,
inspect the reader before a marker (expect neither to be visible), then append
an acceptance marker and inspect again (expect the decided batch). Repeat with
a simulated pre-marker interruption and invoke recovery. This is a proposed
teaching fixture, not a demonstrated production run.

**Evidence and limits.** The local study locates this in
`l0/event_log/event_log.py:157-195,674-712,1900-1966`, with event-log tests
for hidden unsentinelled records and crash-tail truncation; commit `3aaf56904`
is an evolution anchor. It does **not** prove which server writers use Path-A,
that recovery runs in production, or that Path-B is atomic.

**Counterexample.** A single urgent audit record that is intentionally
Path-B-visible does not wait for a batch marker. Calling every event “atomic”
would erase the difference the design makes explicit.

## 2. Commit gate

**Status:** `experimental` · **Mechanism:** `MECH-COMMIT-001`

**One-sentence definition.** A commit gate evaluates a proposed state change
against declared integrity, provenance, ownership, freshness, scope, and audit
conditions before emitting an accepted or rejected outcome.

**Everyday analogy.** It is an airport boarding gate: having a ticket is not
enough if identity, destination, timing, or clearance does not match.

**Real failure it addresses.** An agent can assert a result from a stale
capsule, conflicting worktree, unverified audit, or invalid scope; without a
gate, that assertion can become domain state merely because it was written.

**Mechanism-diagram cue.** Draw
`envelope → checks → {accepted marker | rejected marker | audit pending}` and
show `abandoned envelope → linked rejection + lock release`. Do not collapse
pending audit into acceptance.

**Minimum demo.** Create a neutral envelope fixture with a deliberately stale
freshness value; run the located gate path and show a rejection rather than a
visible accepted outcome. Change only the fixture condition and rerun to show
that the decision depends on the declared checks. This remains a proposed
fixture until sealed and independently replayed.

**Evidence and limits.** The study cites
`l0/commit_gate/gate.py:489-684,1258-1575,2092-2150`, integration cases for
freshness TOCTOU, audit pass/fail, and abandoned-envelope recovery, plus
`0a0b90b44`. It does **not** prove every check is active for every real commit;
the implementation may legitimately mark a check `not_applicable`.

**Counterexample.** A correctly formed envelope is not itself proof that an
agent result is correct. The gate validates its admissibility conditions; it is
not a universal truth oracle or a substitute for independent review.

## 3. Materialized projection and watermark

**Status:** `experimental` · **Mechanism:** `MECH-PROJ-001`

**One-sentence definition.** A materialized projection is a queryable view
rebuilt from committed events; its watermark states how far that view has
processed the event stream.

**Everyday analogy.** Think of a railway departure board: it is useful only if
you know which timetable update it has incorporated, rather than assuming it
is silently current.

**Real failure it addresses.** A reader can otherwise consume a stale or torn
projection file as current state, and a broad repair can damage a healthy view
while trying to fix an unrelated one.

**Mechanism-diagram cue.** Draw
`committed events → reducer → state flush → cursor/meta watermark`; add reader
modes `allow stale | fresh required | watermark at least N`, and a recovery
branch `hash mismatch → single-view recompute → full rebuild only if needed`.

**Minimum demo.** Feed a small accepted event sequence to one disposable
projection, request a watermark above its current cursor (expect refusal),
then replay to that cursor and request again. Corrupt only that view's
state/meta pair and demonstrate a scoped recompute. This is a proposed fixture,
not evidence of complete reducer coverage.

**Evidence and limits.** The study identifies
`l0/projection/projection.py:245-330,756-953`, consistency/tamper/rebuild
tests, and the history anchors `fdbf00034` and `b9324ec4`. It does **not** show
which reducers or consumers are live, and a watermark is not a cross-file
exactly-once transaction guarantee.

**Counterexample.** A projection whose watermark reaches event 80 may still be
the wrong answer for a question that requires event 81. “Fresh enough” is a
declared requirement, not a property inferred from the existence of a view.

## 4. Fencing token (per-key spawn claim)

**Status:** `experimental` · **Mechanism:** `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`

**One-sentence definition.** A fencing token is a per-work-item claim that
lets one eligible executor proceed while making a stale or competing executor
fail its later write or dispatch step.

**Everyday analogy.** It is the numbered ticket at a service counter: a person
holding an older ticket cannot take over merely because they arrived first.

**Real failure it addresses.** Manual and automatic dispatch, or a slow
session misclassified as dead, can start two workers for the same key, spend
twice, and emit conflicting results.

**Mechanism-diagram cue.** Draw
`ready task → liveness/owner/worktree checks → atomic claim + fence → start`,
with `second claimant/stale fence → reject`, and `confirmed DEAD → reap →
redispatch`. Label the scope **per key**, not universal.

**Minimum demo.** Arrange two contenders for a single synthetic task key. Let
the first receive the claim and retain its fence; have the second attempt the
same transition and expect rejection. Then model a confirmed `DEAD` first
session before demonstrating a new claim. It is only a local teaching fixture
unless the real process and server trace are sealed.

**Evidence and limits.** The study points to `l2/orchestrator.py:1558+` and
`9477+`, runtime-sentinel tests, and the stale-lock evolution anchor
`77a3e85fe`. It does **not** prove every dispatch path shares the fence, nor
does it establish kind-level single-flight serialization: the effective set for
that stronger claim is currently empty.

**Counterexample.** Two different task keys may legitimately execute in
parallel. Calling this a global “one agent at a time” lock would both overstate
the mechanism and misdescribe its intended concurrency.

## 5. Dead-letter queue and bounded reflow

**Status:** `experimental` · **Mechanisms:** `M-SESSION-LIVENESS-AND-DEADLETTER`,
`M-REFLOW-OUT-OF-BAND-RECONCILIATION`

**One-sentence definition.** A dead-letter queue isolates work that cannot
continue normally, while bounded reflow records and routes a repair path
without silently retrying or promoting it forever.

**Everyday analogy.** It is a parcel exception desk: an undeliverable package
is not declared delivered, nor is it put back on the truck indefinitely; it is
triaged, rerouted, retired, or escalated.

**Real failure it addresses.** Silence is not reliable evidence of death, and
a successful-looking worktree can remain stranded outside promotion after a
fast watermark window misses it. Unbounded respawn risks cost and conflict;
silent promotion risks a false completed result.

**Mechanism-diagram cue.** Draw
`enqueued → under triage → redispatched | retired | escalated to owner`;
separately draw `stranded worktree → reconciliation → recovery finding →
agent-first repair`. Show `PARKED/STUCK/UNKNOWN` as retained states, not dead.

**Minimum demo.** Use a synthetic task whose session lacks the evidence needed
to call it `DEAD`; show it stays parked/unknown rather than being reaped. Then
inject a stranded-worktree fixture and show that reconciliation emits a finding
instead of directly promoting a main branch. This is a proposed local demo and
must not be described as an enabled server timer.

**Evidence and limits.** Local coordinates include `l1/session_lock.py:225+`,
`l2/orchestrator.py:6283+`, `dead_letter_inbox.py:352+`,
`l2/reflow_reconcile.py:233+`, and `reflow_sentinel.py:90+,385+`, with named
unit/integration tests and history anchors `0feea5bb1` and `d2b787917`. The
sentinel is default-off, inventory subprocess failure may appear empty, and
automatic `revive` is dormant by default. Live timer, inventory, and recovery
evidence are unsealed.

**Counterexample.** A `PARKED` session is not a dead session, and a reflow
finding is not a successful repair. Treating either as automatic completion
would reintroduce the exact false-success path this family is meant to expose.

## 6. Independent verification

**Status:** `experimental` · **Mechanism:** `MECH-VERIFY-001`

**One-sentence definition.** Independent verification assigns a separate
falsifier to test a finding or closure rather than accepting the producer's
self-report as its only evidence.

**Everyday analogy.** It is a second accountant reconciling a ledger: the goal
is not agreement for its own sake, but a separate path that can catch a shared
mistake.

**Real failure it addresses.** A producer can mark its own fix green while a
major residual, omitted scope, or misleading result remains undiscovered.

**Mechanism-diagram cue.** Draw
`finding → separate verifier → pass | fail | timeout | parse error`, with all
non-pass branches feeding `fail closed / fix insufficient`. Keep verification
beside—not inside—the producer lane.

**Minimum demo.** Give a verifier fixture a deliberately incomplete repair and
a falsification criterion it should fail; show the resulting `fix_insufficient`
or non-pass outcome blocks a strong closure. Re-run with a criterion that can
be satisfied only after the missing condition is corrected. This is a proposed
demonstration; it does not prove the real sandbox is physically isolated.

**Evidence and limits.** The study cites `l1/verification_fork.py:1317+`, CLI
paths, run039 tests, and `568d3f057`, `1f9662f2f`, `2fdf34d60`. It explicitly
records that allowed tools include Bash, so a configured read-only policy is
not proof of physical isolation. Server sandbox evidence is required before
making an isolation claim.

**Counterexample.** Two agents using the same unrestricted workspace and the
same unchecked inputs may be separate processes but not meaningfully
independent evidence. “Second agent” alone is not enough.

## 7. Forward provenance anchor

**Status:** `experimental` · **Mechanism:** `MECH-PROV-001`

**One-sentence definition.** A forward provenance anchor is a later,
append-only record that links a frozen earlier record to related evidence
without rewriting the original history.

**Everyday analogy.** It is an amendment in a public meeting record: the
original minutes remain intact, while a dated follow-up tells readers how to
find the supporting material.

**Real failure it addresses.** A frozen `FindingCreated` record may lack a
later evidence-file relationship; mutating it after the fact would hide when
the relationship became known and make reverse lookup unreliable.

**Mechanism-diagram cue.** Draw
`FindingCreated (frozen) ← subjects → RunStarted anchor → evidence file`, with
queries beginning from either the finding or file. Put a visible “no rewrite of
old event” boundary across the original record.

**Minimum demo.** Create a disposable finding record without a file link,
construct a later anchor that names both subjects, and query the subject index
from each end. Attempt an empty anchor and show construction rejection. This is
a proposed isolated fixture, not proof of continuous production provenance.

**Evidence and limits.** The local study names
`l0/event_log/provenance_anchor.py:1-139`, event intent/record schemas, and
`test_prov_anchor.py:151-208`; `d5e4c5aa6` records the append-only choice. It
does **not** show which production writers create anchors or whether every
public claim has a resolvable live trace.

**Counterexample.** An anchor added after a finding can show a later-known
relationship; it cannot prove that the relationship existed or was validated
at the time the original finding was created.

## Use rule

Use these terms in a D0–D9 explanation only with their mechanism ID and an
adjacent evidence/limit link. Do not turn an analogy or minimum demo into a
product claim. The next valid update is a registry-backed revision after an
Evidence Seal, Drift review, and relevant jury check—not copy-editing that
quietly upgrades `experimental` to “live”.
