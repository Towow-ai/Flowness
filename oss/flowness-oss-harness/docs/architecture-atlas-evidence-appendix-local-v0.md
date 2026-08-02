# Architecture Atlas Evidence Appendix — local v0

Status: **local, unsealed static-evidence appendix**. This appendix is the
evidence index for the existing local Architecture Atlas
([`architecture-atlas-local-v0.md`](architecture-atlas-local-v0.md)). It does
not replace a Mechanism Registry, Evidence Seal, runtime trace, deployment
diagram, or public product claim.

Truth boundary: all fifteen seed mechanisms below have a `candidate_mapped`
static chain in one of the five registries listed in §1. The seed registry
caps their *mechanism* status at `experimental`; a static chain only verifies
that the named excerpts still match this checkout. It does **not** promote an
item to `current_verified`, establish rights-cleared public export, or show
that a server process reached the code.

Use this appendix as a reading and review aid:

1. Start at D0–D1 to understand the problem and a *candidate* path.
2. Use D2–D5 to locate a mechanism's state/failure/recovery surfaces.
3. Stop at the stated boundary. D6–D9 are deliberately `UNKNOWN` until a
   sealed source/runtime Evidence Seal and the relevant product evidence exist.

## 1. Evidence set and ceiling

| Static-chain registry | Mapped mechanism IDs | What the registry binds | What it explicitly does not bind |
| --- | --- | --- | --- |
| [Public Mechanism Card registry](../registries/mechanism-cards-v0.json) | `MECH-EVT-001`, `MECH-PROJ-001` | Exported, hash-bound card snapshot; its private derivation inputs are disclosed only by opaque ID and hash | A runtime Ledger caller; a sealed source/export identity |
| [Public Mechanism Cards](mechanism-cards/v0/) | `MECH-EVT-002`, `MECH-OWNER-001`, `MECH-VERIFY-001` | Exported card views with explicit runtime Unknowns | Shared deployed lock, PTY delivery, independent child isolation |
| [Public Mechanism Cards](mechanism-cards/v0/) | `MECH-COMMIT-001`, `MECH-REVIEW-001`, `MECH-CLOSURE-001`, `MECH-CAPSULE-001`, `MECH-PROV-001` | Exported card views with definition/caller/consumer boundaries | Active checks, all review paths, live capsule/anchor producers |
| [Public Mechanism Cards](mechanism-cards/v0/) | `M-ORCH-READYSET-EVENT-FANOUT`, `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`, `M-ORCH-WATERMARK-BACKLOG-DECOUPLING` | Exported orchestration card views; source manifests remain outside Open Alpha | A running daemon, every dispatch route, deployed enforcement/configuration |
| [Public Mechanism Cards](mechanism-cards/v0/) | `M-REFLOW-OUT-OF-BAND-RECONCILIATION`, `M-SESSION-LIVENESS-AND-DEADLETTER` | Exported recovery card views; source manifests remain outside Open Alpha | Enabled timer, inventory/heartbeat evidence, successful runtime recovery |

The shared registry boundary is `UNSEALED-LOCAL-SOURCE;RUNTIME-UNAVAILABLE`.
Every chain contains source excerpt hashes, not evidence of execution. The
seed registry ([`mechanism-registry-seed-v0.json`](../registries/mechanism-registry-seed-v0.json))
is the authoritative list for the fifteen IDs, their `experimental` ceiling,
and their first-order Unknown/Drift references.

## 2. D0 — the real failures these candidates address

D0 is a problem map, not a product-success statement. It groups all fifteen
mechanisms by the failure they were designed to resist; it does not claim these
failures currently occur, or are currently prevented, on any server.

| Candidate failure to make visible | Mechanisms with static local evidence | Static evidence shape | Cannot prove |
| --- | --- | --- | --- |
| A partial, conflicting, or stale result looks canonical | `MECH-EVT-001`, `MECH-EVT-002`, `MECH-COMMIT-001`, `MECH-PROJ-001` | Append/visibility, sequence, gate, projection, recovery, and test excerpts | All writers use Path-A/gate; deployed crash behaviour; user impact |
| Parallel work double-spends or loses ready work at capacity | `M-ORCH-READYSET-EVENT-FANOUT`, `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`, `M-ORCH-WATERMARK-BACKLOG-DECOUPLING` | Orchestrator definition/caller/consumer/recovery and test excerpts | A live scheduler, universal dispatch coverage, actual capacity or throughput |
| A weak review, fix, or context chain gets mistaken for acceptance | `MECH-REVIEW-001`, `MECH-CLOSURE-001`, `MECH-CAPSULE-001`, `MECH-PROV-001`, `MECH-VERIFY-001` | Review/closure/capsule/provenance/verify excerpts plus tests | All reviews pass the same gates; effective verifier separation; real prompt delivery |
| Failed, stranded, or owner-dependent work vanishes or revives unsafely | `M-REFLOW-OUT-OF-BAND-RECONCILIATION`, `M-SESSION-LIVENESS-AND-DEADLETTER`, `MECH-OWNER-001` | Reconciliation, liveness/dead-letter, owner-reflow, recovery, and test excerpts | Enabled sentinels; delivery to a live session; an end-to-end recovery outcome |

The mechanism-level rationale, historical anchors, failure mode, and open
question remain in [`local-mechanism-study-v0.md`](local-mechanism-study-v0.md).
That study is a source guide; the static manifests are the checkable excerpt
bindings for this checkout.

## 3. D1 — candidate goal-to-result path

The existing D1 diagram can be read as the following **unproven composition**:

```text
goal / plan
  -> ready-set and claim candidate
  -> worker/worktree boundary (runtime unknown)
  -> envelope/gate/event/projection candidate
  -> review/closure/verification candidate
  -> accepted result only after evidence not yet sealed

failure -> rejection, reflow, dead letter, or owner-answer candidate
        -> governed retry or retained escalation
```

| D1 segment | Evidence-bearing IDs | Static status | Unresolved join |
| --- | --- | --- | --- |
| Plan readiness and dispatch decision | `M-ORCH-READYSET-EVENT-FANOUT`, `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`, `M-ORCH-WATERMARK-BACKLOG-DECOUPLING` | `candidate_mapped` / seed `experimental` | No trace connects every plan/task type to an actual worker launch |
| Work/context/independent check | `MECH-CAPSULE-001`, `MECH-VERIFY-001` | `candidate_mapped` / seed `experimental` | No sealed context-to-prompt-to-result or physically independent verifier trace |
| Commit and readable state | `MECH-COMMIT-001`, `MECH-EVT-001`, `MECH-EVT-002`, `MECH-PROJ-001` | `candidate_mapped` / seed `experimental` | No runtime writer→gate→sentinel→projection trace; Path-B/raw-port boundary remains weaker |
| Review and closure | `MECH-REVIEW-001`, `MECH-CLOSURE-001`, `MECH-PROV-001` | `candidate_mapped` / seed `experimental` | No complete server multi-session finding/retest/closure chain; known closure integration drift remains open |
| Failure and return/escalation | `M-REFLOW-OUT-OF-BAND-RECONCILIATION`, `M-SESSION-LIVENESS-AND-DEADLETTER`, `MECH-OWNER-001` | `candidate_mapped` / seed `experimental` | No enabled sentinel, recovery finding, delivery, or resumed-work trace |

Therefore D1 must never be captioned “the current Flowness workflow.” It is a
cross-layer hypothesis assembled from separately located static chains.

## 4. D2 — lifecycle/state evidence index

The state names in D2 are explanatory labels. Only the named local excerpts
are statically bound; the table does not assert one unified state machine.

| Candidate D2 state or transition | Mechanisms that locate part of it | Failure / recovery that must stay attached | Cannot prove |
| --- | --- | --- | --- |
| `Planned → Ready → Claimed` | `M-ORCH-READYSET-EVENT-FANOUT`, `M-ORCH-SINGLE-EXEC-CLAIM-FENCING` | Non-success must not unlock; failed claim/preparation must not stamp success | Every task or manual path enters the same ready/claim protocol |
| `Claimed → Running → EnvelopePending` | `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`, `MECH-CAPSULE-001`, `MECH-COMMIT-001` | Stale/invalid envelope can be rejected; missing capsule dependency aborts assembly | Real worktree preparation, spawn, session identity, or capsule injection |
| `EnvelopePending → CommitAccepted/Rejected → Projected` | `MECH-COMMIT-001`, `MECH-EVT-001`, `MECH-EVT-002`, `MECH-PROJ-001` | Abandoned/audit/stale rejection; orphan-tail/replay/torn-projection recovery | A deployed event schema, all producer/consumer paths, or end-to-end atomicity |
| `Projected → UnderReview → Closed/Rework` | `MECH-REVIEW-001`, `MECH-CLOSURE-001`, `MECH-VERIFY-001`, `MECH-PROV-001` | Unresolved finding, failed/timeout verifier, residual/forged/unrecomputable closure | A live independent review lifecycle or production closure rate |
| `Running/Stranded → RecoveryFinding → Ready/OwnerEscalated` | `M-REFLOW-OUT-OF-BAND-RECONCILIATION`, `M-SESSION-LIVENESS-AND-DEADLETTER`, `MECH-OWNER-001` | Dead-letter cap/TTL, mint-cap escalation, unresolved delivery are retained; auto-revive is not promoted | Active timer/sentinel, actual PTY delivery, or successful promotion of every stranded worktree |

## 5. D3 — candidate planes, with boundary ownership

| Plane | Statically mapped mechanisms | What the local evidence can locate | Boundary that remains `UNKNOWN` |
| --- | --- | --- | --- |
| Control | `M-ORCH-READYSET-EVENT-FANOUT`, `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`, `M-ORCH-WATERMARK-BACKLOG-DECOUPLING`, `MECH-OWNER-001` | Readiness, dispatch decision, per-key fence, backlog marker, and owner-answer calculation paths | Daemon cadence, configured enforce-on state, authority enforcement, actual owner/session delivery |
| Execution | `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`, `MECH-CAPSULE-001`, `MECH-VERIFY-001` | Worktree/session preparation references, capsule assembly, verifier admission/fail-closed code | Real agent spawn, account/session identity, model/tool boundary, sandbox effectiveness, capacity |
| Evidence | `MECH-EVT-001`, `MECH-EVT-002`, `MECH-COMMIT-001`, `MECH-PROJ-001`, `MECH-REVIEW-001`, `MECH-CLOSURE-001`, `MECH-PROV-001` | Event, gate, projection, finding/closure, forward-anchor code/test paths | Sealed canonical log, live readers/writers, complete reducer coverage, all-write coverage |
| Recovery and governance | `M-REFLOW-OUT-OF-BAND-RECONCILIATION`, `M-SESSION-LIVENESS-AND-DEADLETTER`, `MECH-CLOSURE-001`, `MECH-OWNER-001` | Finding/dead-letter/reconciliation/escalation branches in local source | Enabled timer/service, inventory quality, retry outcome, rollback topology, server owner channel |

“Execution plane” is particularly easy to overstate: it is an **interface and
candidate-code boundary**, not evidence that a remote Codex/Claude worker was
spawned or that isolation was effective.

## 6. D4 — mechanism family coverage and exact static anchors

Each ID below has one exported Mechanism Card. The private static-chain
manifests used to derive those snapshots are intentionally withheld from Open
Alpha; their opaque IDs and hashes preserve change detection without creating
a broken local link. “Definition → caller → consumer → recovery/failure →
test” describes the card's bounded node roles, not a claim that the whole route
executed together.

| Family | IDs | Static manifest / located anchor family | Known limiting condition |
| --- | --- | --- | --- |
| Ledger and projection | `MECH-EVT-001`, `MECH-EVT-002`, `MECH-PROJ-001` | Ledger candidate `ledger.py` / `projection.py`; private EventLog `event_log.py`; associated ledger/concurrency/projection tests | Ledger projection's direct caller is only a test; raw append is a weaker path; no live writer/reader trace |
| Commit and assurance | `MECH-COMMIT-001`, `MECH-REVIEW-001`, `MECH-CLOSURE-001`, `MECH-VERIFY-001` | `commit_gate/gate.py`, `review_verdict.py`, `closure_verification.py`, `verification_fork.py`, CLI/check consumers and tests | Active checks, all review routes, true verifier independence, and closure E2E are not established |
| Context and provenance | `MECH-CAPSULE-001`, `MECH-PROV-001` | `capsule/pipeline.py`, `provenance_anchor.py`, audit/helper/test consumers | No deployed capsule prompt consumer; no non-helper production anchor caller |
| Orchestration | `M-ORCH-READYSET-EVENT-FANOUT`, `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`, `M-ORCH-WATERMARK-BACKLOG-DECOUPLING` | `l2/orchestrator.py`, fencing check, orchestration tests | Runtime driver, complete dispatch coverage, enforce state, capacity telemetry unknown |
| Recovery and owner return | `M-REFLOW-OUT-OF-BAND-RECONCILIATION`, `M-SESSION-LIVENESS-AND-DEADLETTER`, `MECH-OWNER-001` | `reflow_reconcile.py`, `reflow_sentinel.py`, liveness/dead-letter code, `escalation_reflow.py`, tests | Sentinel/default-off recovery state, live liveness records, delivery/provenance chain unknown |

The required historical “why” and failure/recovery narrative is intentionally
not compressed into the table. Follow each ID to
[`local-mechanism-study-v0.md`](local-mechanism-study-v0.md) and then to the
corresponding static manifest before turning it into any external explanation.

## 7. D5 — candidate sequences and the evidence missing from each

| Candidate sequence | Static chain permits a reviewer to inspect | Evidence required before a runtime sequence claim |
| --- | --- | --- |
| Commit → sentinel/visible batch → projection/watermark | `MECH-COMMIT-001`, `MECH-EVT-001`, `MECH-EVT-002`, `MECH-PROJ-001` source/test excerpts, including rejection and recovery branches | Sealed event segments, process identity, gate decision, projection cursor/watermark, readback and recovery trace from one run |
| Ready set → claim fence → work preparation → dispatch decision | All three `M-ORCH-*` chains, including test paths and their code-level recovery branches | A redacted daemon/session/worktree trace with configured fence state and an actual decision/outcome |
| Review/finding → independent check → closure/retest | `MECH-REVIEW-001`, `MECH-VERIFY-001`, `MECH-CLOSURE-001`, `MECH-PROV-001` chains | A sealed finding, independent execution identity/effective boundary, verdict, resolution, retest and terminal acceptance/rejection trace |
| Stranded work → reflow/dead letter → owner return or governed retry | Recovery chains plus `MECH-OWNER-001` | Timer/service identity, selected failure record, recovery finding, delivery confirmation or terminal escalation, and postcondition |

The diagrams in `architecture-atlas-local-v0.md` remain **trace templates**.
They cannot be relabeled as recordings without the matching Evidence Seal set.

## 8. D6–D9 — intentionally not drawn as implemented

| Layer | Current status | What is missing before it can become an evidence-bearing view |
| --- | --- | --- |
| D6 deployment, fault domains, rollback | `UNKNOWN` | Sealed deployed unit/config/process identities, enabled/disabled state, host/failure-domain mapping, selected incident/recovery evidence |
| D7 authority, data, credentials, owner gates | `UNKNOWN` | Write-path inventory, effective service permissions, credential/data-flow review, network/sandbox evidence, owner-authority traces |
| D8 end-to-end provenance | `UNKNOWN` | A redacted trace joining event/envelope/sentinel/watermark/capsule/finding/acceptance and claim evidence without gaps |
| D9 current/target/release roadmap | `UNKNOWN` for implementation/deployment recommendation | Evidence Seal, rights-cleared export, clean-room installation, acceptance/eval data, and independent jury results; the local readiness route is not release authorization |

The seed registry's blocking Unknowns (`UNKNOWN-SERVER-RUNTIME-001`,
`UNKNOWN-PUBLIC-API-001`, `UNKNOWN-HOOK-LIVENESS-001`, and
`UNKNOWN-WOW-CONTINUITY-001`) remain blockers for their respective claims.
No D6–D9 diagram should fill these gaps with inferred hostnames, services,
security controls, public API surface, or legacy continuity.

## 9. Reviewer checklist for a future evidence update

Before changing a caption from `experimental`/`candidate_mapped` to a stronger
status, record all of the following against the specific mechanism ID:

- a sealed source identity and rights-cleared export boundary;
- a relevant live event/runtime trace, including real producer and consumer;
- the state transition plus at least one failure/recovery or explicit
  no-occurrence result;
- deployment/configuration proof where the mechanism depends on a daemon,
  timer, sandbox, permission, or service identity;
- a registry update that preserves remaining Unknowns and any Drift finding;
- review of every downstream diagram, claim, demo, and channel asset through
  the Content Graph before any external wording changes.

Until then, the most precise public-safe statement is: **a selected local
implementation study has statically mapped fifteen candidate mechanisms; their
runtime integration, deployment, and release readiness remain unsealed.**
