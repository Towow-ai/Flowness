# conservative liveness and dead-letter terminalization (M-SESSION-LIVENESS-AND-DEADLETTER)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `da577f8fe461` (superseded): The old session-lock signal history is explicitly superseded by the current session-liveness node.
- `4a0052263fb3` (introduced): Introduced the current dead-letter enqueue node.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 1 AST-bound edge(s) and 2 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Static failure coordinates are mapped; their runtime reachability and full failure coverage are Unknown.
- **recovery boundary**: Static recovery coordinates are mapped; deployed scheduling, success and rollback behavior are Unknown.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `static.caller.1.c5ec21291d55cfc7`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.d4e8626a1e49c05f`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `liveness001-reconciler-calls-assessment`: `static.caller.1.c5ec21291d55cfc7` — calls → `static.definition.1.d4e8626a1e49c05f`; AST call `{'form': 'name', 'symbol': 'assess_session_liveness'}` at `harness/src/towow/l2/orchestrator.py:6255-6307 (sha256:c5ec21291d55cfc7d4e316f312bd2c89de34b5169580e45efe9bfa0780a50e76)` targets `towow.l2.session_liveness.assess_session_liveness` at `harness/src/towow/l2/session_liveness.py:194-257 (sha256:d4e8626a1e49c05f33d9c67a879d8af75dd7def50fc6a5322d9222368e0cfbfe)` via `direct_import_name`. Proves: The reconciler directly imports and calls the mapped liveness assessor. Does not prove: It does not prove a live session verdict.
- `liveness001-assessment-to-orchestrator-consumer-unknown`: `static.definition.1.d4e8626a1e49c05f` — unknown → `unknown.output-consumer`; Unknown relation. Next evidence: A source relation that links the assessor result to the mapped consumer section, plus a sealed dead-letter trace for terminal behavior. Boundary: No dead-letter terminalization or recovery is established.
- `liveness001-deadletter-recovery-specific-unknown`: `static.recovery.1.37d9c9fac0e9fc50` — unknown → `unknown.failure-handoff`; Unknown relation. Next evidence: A failure-to-recovery linkage between redispatch failure and the mapped aging sweep. Boundary: No retry, terminalization, or rollback behavior is established.
### Objects and evidence boundary
- `static.definition.1.d4e8626a1e49c05f` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/session_liveness.py:194-257 (sha256:d4e8626a1e49c05f33d9c67a879d8af75dd7def50fc6a5322d9222368e0cfbfe)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.definition.2.77e7cc03397d8fe3` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/dead_letter_inbox.py:352-393 (sha256:77e7cc03397d8fe337dc0d1b75cfe70e23dd78645b133774d862d6a478bd787d)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.c5ec21291d55cfc7` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l2/orchestrator.py:6255-6307 (sha256:c5ec21291d55cfc7d4e316f312bd2c89de34b5169580e45efe9bfa0780a50e76)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.2.8cd3626ada1de016` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l2/orchestrator.py:10770-10779 (sha256:8cd3626ada1de0164249e51ba13ad0ff686043cb625ce5937583800ef2750597)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.ca0d3ab857262d84` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l2/orchestrator.py:6308-6447 (sha256:ca0d3ab857262d844aee3c0cbf3b5c2d58563bdc7ba14d2b4efbbd5a348a909b)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.42e5a3418bba54f8` (static_coordinate, test, local_static_candidate): `withheld:coordinate-834c9d2be22a25fb7b47 (sha256:42e5a3418bba54f8daef8fb7a977ad7bc95d9b7e66660d92acac02c614e15edb; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.2.67d3a5f18b8b39e7` (static_coordinate, test, local_static_candidate): `withheld:coordinate-869570ae6dc2a2212ceb (sha256:67d3a5f18b8b39e79989cec1170fdd0848ac874cc06ccfa58cffa8c4b3f802a4; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.3.73e1ca7629bc8fd0` (static_coordinate, test, local_static_candidate): `withheld:coordinate-ae6ca69c56e95e2f445a (sha256:73e1ca7629bc8fd05506dc62f13c0b78dd495e60df7b17a341eed8612661758b; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.failure.1.21b9cb804afca0ce` (static_coordinate, failure, local_static_candidate): `harness/src/towow/l2/dead_letter_inbox.py:531-567 (sha256:21b9cb804afca0ce8a6e8d684c4a3ba12bec8089510a86bbcaa4706d0db2df7e)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.37d9c9fac0e9fc50` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l2/dead_letter_inbox.py:629-652 (sha256:37d9c9fac0e9fc50265e6461c03559e986efcb29241459d766971db6723a54e3)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `M-SESSION-LIVENESS-AND-DEADLETTER.static-failure-1` (located_static_failure_boundary): detect `static.failure.1.21b9cb804afca0ce`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.37d9c9fac0e9fc50` (candidate_recovery_boundary, local_static_candidate); negative-test locator `static.test.1.42e5a3418bba54f8`. Boundary: Static location only; trigger, retry budget, idempotence, and runtime outcome remain Unknown.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l2/session_liveness.py:194-257 (sha256:d4e8626a1e49c05f33d9c67a879d8af75dd7def50fc6a5322d9222368e0cfbfe)`
- `harness/src/towow/l2/dead_letter_inbox.py:352-393 (sha256:77e7cc03397d8fe337dc0d1b75cfe70e23dd78645b133774d862d6a478bd787d)`
### caller
- `harness/src/towow/l2/orchestrator.py:6255-6307 (sha256:c5ec21291d55cfc7d4e316f312bd2c89de34b5169580e45efe9bfa0780a50e76)`
- `harness/src/towow/l2/orchestrator.py:10770-10779 (sha256:8cd3626ada1de0164249e51ba13ad0ff686043cb625ce5937583800ef2750597)`
### consumer
- `harness/src/towow/l2/orchestrator.py:6308-6447 (sha256:ca0d3ab857262d844aee3c0cbf3b5c2d58563bdc7ba14d2b4efbbd5a348a909b)`
### test
- `withheld:coordinate-428287eb8b07f7c54648 (sha256:42e5a3418bba54f8daef8fb7a977ad7bc95d9b7e66660d92acac02c614e15edb; source withheld from Open Alpha)`
- `withheld:coordinate-d1d168945766e76c5631 (sha256:67d3a5f18b8b39e79989cec1170fdd0848ac874cc06ccfa58cffa8c4b3f802a4; source withheld from Open Alpha)`
- `withheld:coordinate-ac0c1959690b88ac76c2 (sha256:73e1ca7629bc8fd05506dc62f13c0b78dd495e60df7b17a341eed8612661758b; source withheld from Open Alpha)`
### failure
- `harness/src/towow/l2/dead_letter_inbox.py:531-567 (sha256:21b9cb804afca0ce8a6e8d684c4a3ba12bec8089510a86bbcaa4706d0db2df7e)`
### recovery
- `harness/src/towow/l2/dead_letter_inbox.py:629-652 (sha256:37d9c9fac0e9fc50265e6461c03559e986efcb29241459d766971db6723a54e3)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-TEST-RUNTIME-003`
### Unresolved
- Is automatic revive live or deliberately dormant?
- No sealed source snapshot or rights-cleared public export binds this local implementation.
- This chain maps liveness cleanup and dead-letter terminalization subpaths; it does not prove a single direct runtime edge from every liveness verdict to every dead-letter transition.
- No server heartbeat, PID, roster, pending-marker, dead-letter, owner-response, or TTL-sweep trace proves deployed behavior or rates.
- Automatic revival remains outside this chain; its deployed enablement and safety budget are unverified.
- Static tests do not prove that every dispatch failure is classified, routed, or terminalized in production.
- Revival and revive-marker actions now enter through portable_runtime adapters; this chain does not map their implementation or prove equivalence with the former private module path.
- These local links do not prove automatic revival is enabled or safe in a live deployment.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.M-SESSION-LIVENESS-AND-DEADLETTER`
- Candidate asset node: `asset.mechanism-card.m-session-liveness-and-deadletter`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
