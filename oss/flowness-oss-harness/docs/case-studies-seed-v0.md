# Flowness case-study seeds — v0

Status: local code/history evidence candidates. None is a production customer
case, performance result, or proof that the described recovery has fired on a
current server. Each seed is designed to become a public case only after its
corresponding Evidence Seal and claim review.

## Case A — a half-written change must not look complete

| Field | Evidence-bounded seed |
| --- | --- |
| Audience problem | A reader sees an event log and assumes every appended domain record is final. A crash partway through a multi-record decision can make a partial change look like complete history. |
| Local mechanism | `MECH-EVT-001` committed-visible batch ledger, with `MECH-COMMIT-001` envelope-to-gate decision. |
| Baseline failure | A physical append is treated as a semantic commit before the decision outcome is known. |
| Candidate flow | Proposed domain records remain pending; an accepted/rejected sentinel determines visibility; incomplete crash tails are recovered without inventing acceptance. |
| Authoritative outcome | The local test/code study says a committed reader excludes unsentinelled Path-A records and recovery truncates a crash tail. |
| Recovery | Tail recovery preserves the visible committed boundary; rejection is an explicit terminal state. |
| Public lesson | “Durable bytes” and “a change we are allowed to rely on” are different things. |
| Limitation | Path-B is individually durable, not batch atomic; no server crash frequency or production writer coverage is sealed. |
| Promotion evidence | Commit/tree identity, selected real event/sentinel/projection chain, a redacted crash/recovery trace or explicit no-occurrence record, and code/test evidence. |

## Case B — capacity pressure must not repeatedly rediscover the same work

| Field | Evidence-bounded seed |
| --- | --- |
| Audience problem | A task becomes ready while capacity is full. If a watermark stays behind, the same event tail can be rescanned while downstream work starves. |
| Local mechanism | `M-ORCH-WATERMARK-BACKLOG-DECOUPLING`, plus `M-ORCH-SINGLE-EXEC-CLAIM-FENCING`. |
| Baseline failure | Retry means “read the whole tail again,” and duplicate dispatch becomes possible when liveness is inferred from silence. |
| Candidate flow | A deferred decision receives a durable backlog marker while the watermark advances; a later governed retry reconstructs it. Per-key claims fence a second dispatch. |
| Authoritative outcome | Local code/test/history coordinates identify backlog markers, idempotent replay and a prior CPU/capacity incident as the load-bearing rationale. |
| Recovery | Markers clear after dispatch or observed output; dead/stale session handling remains conservative. |
| Public lesson | A retry queue should preserve why work was deferred, not convert pressure into repeated scans or duplicate actors. |
| Limitation | It is not a global exactly-once transaction; real agent spawning defaults to mock in local evidence and live capacity data is unsealed. |
| Promotion evidence | One redacted server chain showing event → defer marker → watermark advance → governed retry/outcome, plus service configuration and a failure/recovery postcondition. |

## Case C — a green-looking fix must face an independent closure contract

| Field | Evidence-bounded seed |
| --- | --- |
| Audience problem | A fixer can self-report success even when a major review finding, residual pattern or omitted scope remains. |
| Local mechanism | `MECH-REVIEW-001` event-folded finding lifecycle, `MECH-CLOSURE-001` contract-side recomputation, and `MECH-VERIFY-001` independent verification fork. |
| Baseline failure | “Task completed” becomes a proxy for “the stated concern is resolved.” |
| Candidate flow | Finding creation records falsification and closure criteria; verification/review events fold conservatively; closure recomputes declared checks rather than trusting the producer's summary. |
| Authoritative outcome | Local tests/code support conservative verdict folding and a closure contract; a narrowed integration test mismatch is retained as Drift, not hidden. |
| Recovery | Failed/unrecomputable closure remains visible and returns to bounded rework/retest; unresolved major findings do not pass. |
| Public lesson | A completion message is not the same as evidence that the specified failure was removed. |
| Limitation | Independent roles are logical/code-level evidence only; physical read-only isolation and full live multi-session chains require server proof. |
| Promotion evidence | A sealed card-to-event-to-recomputed-output chain, independent verifier identity/attestation, and a failure case that remains failed until the declared retest passes. |

## Case D — noticing an owner answer is not the same as delivering it

| Field | Evidence-bounded seed |
| --- | --- |
| Audience problem | A long-running task is parked for a human decision; the answer exists somewhere, but the original session does not receive it or has already died. |
| Local mechanism | `MECH-OWNER-001` owner-answer reflow with capsule/provenance context. |
| Baseline failure | An interaction layer labels an answer “handled” without proving delivery to the parked consumer. |
| Candidate flow | Only a `PARKED_RESUMABLE` session with transcript delivery confirmation becomes applied; other vitality states retain a retry, stranded or terminal path. |
| Authoritative outcome | Local modules and tests establish the proposed state distinctions and fail paths. |
| Recovery | Dead/unknown/failed-delivery paths remain distinct; they do not silently become resumed work. |
| Public lesson | Human-in-the-loop means a traceable return path, not merely a UI response. |
| Limitation | Live PTY delivery, raw Transcript and canonical server events are expressly unsealed and excluded from this case today. |
| Promotion evidence | An owner-approved, redacted delivery chain with session token, event/projection postcondition and no raw conversation content. |

## Packaging rule

Any public case must retain its explicit limitation, evidence status and
“cannot prove” line. Quantitative efficiency, customer value, install success
or adoption metrics may be added only to a case with raw trial evidence and a
matching benchmark/claim record.
