# event-folded review finding lifecycle (MECH-REVIEW-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `bbc57c24462f` (superseded): The former cross-session review fold hunk is declared superseded by the present static fold node.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 1 AST-bound edge(s) and 1 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Unknown: no distinct static failure coordinate is mapped.
- **recovery boundary**: Static recovery coordinates are mapped; deployed scheduling, success and rollback behavior are Unknown.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `unknown.trigger-input`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.9257f568f08275dd`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `review001-fold-calls-apply-event`: `static.definition.1.9257f568f08275dd` — calls_internal_helper → `static.definition.1.9257f568f08275dd`; AST call `{'form': 'name', 'symbol': '_apply_event'}` at `harness/src/towow/l0/projection/review_verdict.py:72-137 (sha256:9257f568f08275ddf2bc3cf244d35826b146db051c2554173586f4cee101b11c)` targets `towow.l0.projection.review_verdict._apply_event` at `harness/src/towow/l0/projection/review_verdict.py:72-137 (sha256:9257f568f08275ddf2bc3cf244d35826b146db051c2554173586f4cee101b11c)` via `same_module_name`. Proves: fold_review_verdict calls the mapped module-level _apply_event declaration. Does not prove: It does not establish a committed finding lifecycle.
- `review001-gate-consumer-link-unknown`: `static.definition.1.9257f568f08275dd` — unknown → `unknown.output-consumer`; Unknown relation. Next evidence: A source relation or sealed trace linking the mapped review fold result to the gate consumer call site. Boundary: No gate enforcement behavior is established.
### Objects and evidence boundary
- `static.definition.1.9257f568f08275dd` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l0/projection/review_verdict.py:72-137 (sha256:9257f568f08275ddf2bc3cf244d35826b146db051c2554173586f4cee101b11c)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.5806a2c0919f5590` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l0/commit_gate/review_verdict_check.py:142-248 (sha256:5806a2c0919f5590e25313468c48aa73d3956f24abb2f852f1e41e6e74c0bcbf)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.2f3a812107b15c6f` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l0/commit_gate/gate.py:1626-1644 (sha256:2f3a812107b15c6f1c3f0f287942a9240e2e6047e8d920d420bb5a286f2dcfe0)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.123dac6315dc5484` (static_coordinate, test, local_static_candidate): `harness/tests/unit/l0/test_lnd03_review_verdict_fold.py:78-93 (sha256:123dac6315dc54841a00067d6fefd1d82afba03afcb3de0ef2a3f3e7edb22142)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.fd9a8680c08ab1bf` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l0/projection/review_verdict.py:99-128 (sha256:fd9a8680c08ab1bf02f842d09a5638ca41f3b96fd0cb2ba5bd82f97c1ad7574a)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `unknown.trigger-input` (trigger_input, Unknown, unknown): Which runtime trigger/input actually enters this mechanism?
- `unknown.authoritative-state` (authoritative_state, Unknown, unknown): Which runtime state/store is authoritative for this mechanism?
- `unknown.effective-authority` (authority, Unknown, unknown): Which effective permission/owner can invoke, retry, or terminate this path?
- `unknown.terminal-policy` (terminal_policy, Unknown, unknown): Which terminal/non-terminal disposition applies after this mechanism reaches its boundary?
- `unknown.output-consumer` (output_consumer, Unknown, unknown): Which runtime consumer receives this mechanism output?
- `unknown.failure-detection` (failure_detection, Unknown, unknown): Which runtime signal detects this mechanism failure?
- `unknown.failure-owner` (failure_owner, Unknown, unknown): Which runtime actor owns the failure disposition?
- `unknown.failure-handoff` (failure_handoff, Unknown, unknown): Which runtime recovery or handoff is causally linked to the failure?
- Authority: `unknown.effective-authority` (unknown); next evidence: A sealed runtime observation showing the effective actor and gate decision.

## Failure and recovery disposition
- `MECH-REVIEW-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.fd9a8680c08ab1bf` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.123dac6315dc5484`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l0/projection/review_verdict.py:72-137 (sha256:9257f568f08275ddf2bc3cf244d35826b146db051c2554173586f4cee101b11c)`
### caller
- `harness/src/towow/l0/commit_gate/review_verdict_check.py:142-248 (sha256:5806a2c0919f5590e25313468c48aa73d3956f24abb2f852f1e41e6e74c0bcbf)`
### consumer
- `harness/src/towow/l0/commit_gate/gate.py:1626-1644 (sha256:2f3a812107b15c6f1c3f0f287942a9240e2e6047e8d920d420bb5a286f2dcfe0)`
### test
- `harness/tests/unit/l0/test_lnd03_review_verdict_fold.py:78-93 (sha256:123dac6315dc54841a00067d6fefd1d82afba03afcb3de0ef2a3f3e7edb22142)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l0/projection/review_verdict.py:99-128 (sha256:fd9a8680c08ab1bf02f842d09a5638ca41f3b96fd0cb2ba5bd82f97c1ad7574a)`

## Drift and unresolved questions
### Applicable drift IDs
- None declared by the seed registry.
### Unresolved
- How do multi-session chains resolve in server evidence?
- No sealed multi-session server event chain establishes how cross-session finding resolution behaves in production.
- The static chain proves code and tests, not that every review completion reaches this gate.
- This superseded local anchor does not prove live multi-session review resolution chains.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-REVIEW-001`
- Candidate asset node: `asset.mechanism-card.mech-review-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
