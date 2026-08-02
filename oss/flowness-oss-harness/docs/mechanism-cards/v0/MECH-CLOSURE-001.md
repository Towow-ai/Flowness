# contract-side closure recomputation (MECH-CLOSURE-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `7585511d0b6e` (superseded): A historical closure dialect fix is retained as superseded context for the current verification node.
- `f64c4d2cc98a` (superseded): A historical missing-residual safeguard is retained as superseded context for the current verification node.
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
- Trigger/input object: `static.caller.1.fc550528f4679844`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.b531beeeda9c14ea`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `closure001-recompute-calls-verifier`: `static.caller.1.fc550528f4679844` — calls → `static.definition.1.b531beeeda9c14ea`; AST call `{'form': 'name', 'symbol': 'verify_closure_against_contract'}` at `harness/src/towow/l1/execution_done_recompute.py:185-219 (sha256:fc550528f4679844464b7523591ef582e4e7ca8dd1b5b6dfd03375b9c23638b6)` targets `towow.l1.closure_verification.verify_closure_against_contract` at `harness/src/towow/l1/closure_verification.py:843-1080 (sha256:b531beeeda9c14ea5c3c8f9c29b264f5b003ffb06eadcf2a3ed6cd52abad88e5)` via `direct_import_name`. Proves: The mapped execution recompute range directly imports and calls the mapped closure verifier. Does not prove: It does not prove a closure outcome or a live gate.
- `closure001-verifier-recomputes-criteria`: `static.definition.1.b531beeeda9c14ea` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped _recompute_criterion target coordinate or a sealed closure evaluation trace. Boundary: It does not identify an authoritative closure state.
### Objects and evidence boundary
- `static.definition.1.b531beeeda9c14ea` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l1/closure_verification.py:843-1080 (sha256:b531beeeda9c14ea5c3c8f9c29b264f5b003ffb06eadcf2a3ed6cd52abad88e5)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.fc550528f4679844` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l1/execution_done_recompute.py:185-219 (sha256:fc550528f4679844464b7523591ef582e4e7ca8dd1b5b6dfd03375b9c23638b6)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.23820f579b002ce3` (static_coordinate, consumer, local_static_candidate): `withheld:coordinate-6d48ef9cd11a0ec0ce40 (sha256:23820f579b002ce324c057a53b58dcf6a748d61eca3a30ba8f7d53f076dcb7f5; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.f586aa66d00bd42a` (static_coordinate, test, local_static_candidate): `withheld:coordinate-59b062bc4d39bc10d58c (sha256:f586aa66d00bd42a79a53a3bea83a7ea3d81940aa29af1707e9a813f0cdbec19; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.262ea63f377869a0` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l1/closure_verification.py:1082-1117 (sha256:262ea63f377869a020ce0e440bffae2c3fa49a77cfc90c4672051541100eb393)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-CLOSURE-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.262ea63f377869a0` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.f586aa66d00bd42a`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l1/closure_verification.py:843-1080 (sha256:b531beeeda9c14ea5c3c8f9c29b264f5b003ffb06eadcf2a3ed6cd52abad88e5)`
### caller
- `harness/src/towow/l1/execution_done_recompute.py:185-219 (sha256:fc550528f4679844464b7523591ef582e4e7ca8dd1b5b6dfd03375b9c23638b6)`
### consumer
- `withheld:coordinate-1d1c4e9a5e9762c7ff1a (sha256:23820f579b002ce324c057a53b58dcf6a748d61eca3a30ba8f7d53f076dcb7f5; source withheld from Open Alpha)`
### test
- `withheld:coordinate-0d92729f0b4e50fc853b (sha256:f586aa66d00bd42a79a53a3bea83a7ea3d81940aa29af1707e9a813f0cdbec19; source withheld from Open Alpha)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l1/closure_verification.py:1082-1117 (sha256:262ea63f377869a020ce0e440bffae2c3fa49a77cfc90c4672051541100eb393)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-DOC-CODE-005`
### Unresolved
- Why does the narrowed integration case disagree with the closure expectation?
- The known narrowed integration mismatch for closure is not cleared by this static evidence.
- No sealed runtime trace proves the authoritative closure command ran against a real worktree and committed ledger.
- These superseded local anchors do not clear the known narrowed CLI integration drift.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-CLOSURE-001`
- Candidate asset node: `asset.mechanism-card.mech-closure-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
