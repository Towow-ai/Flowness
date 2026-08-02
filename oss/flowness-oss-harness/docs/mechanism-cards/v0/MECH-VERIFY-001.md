# independent verification fork (MECH-VERIFY-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `568d3f0574fa` (introduced): Introduced the current unified verification-fork dispatcher.
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
- Trigger/input object: `static.caller.1.644052be4d0249b2`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.a7edf35f45b1a091`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `verify001-cli-calls-dispatch`: `static.caller.1.644052be4d0249b2` — calls → `static.definition.1.a7edf35f45b1a091`; AST call `{'form': 'name', 'symbol': 'dispatch_fork'}` at `withheld:coordinate-98088c0585b1708e088f (sha256:644052be4d0249b2b9baa2ec81dc27d0a09a3d2fd250ff7f45c90d1ee026653c; source withheld from Open Alpha)` targets `towow.l1.verification_fork.dispatch_fork` at `harness/src/towow/l1/verification_fork.py:1662-1760 (sha256:a7edf35f45b1a0919a1662c112468df40d535ae38f70b9ed2048cd04ebfb1da5)` via `direct_import_name`. Proves: The CLI closure flow directly imports and calls the mapped verification dispatch declaration. Does not prove: It does not prove a fork was launched.
- `verify001-dispatch-calls-runner`: `static.definition.1.a7edf35f45b1a091` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped run_verification_fork target coordinate and caller binding, or a sealed fork trace. Boundary: The runner is not a mapped state/consumer object and no runtime fork result is asserted.
### Objects and evidence boundary
- `static.definition.1.a7edf35f45b1a091` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l1/verification_fork.py:1662-1760 (sha256:a7edf35f45b1a0919a1662c112468df40d535ae38f70b9ed2048cd04ebfb1da5)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.644052be4d0249b2` (static_coordinate, caller, local_static_candidate): `withheld:coordinate-98088c0585b1708e088f (sha256:644052be4d0249b2b9baa2ec81dc27d0a09a3d2fd250ff7f45c90d1ee026653c; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.c1c3e36360281442` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l1/verification_fork.py:1521-1546 (sha256:c1c3e36360281442334560e89fb3d19404d3657f91094586d227c973f85a16cb)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.2223792564167216` (static_coordinate, test, local_static_candidate): `withheld:coordinate-eeade850522b307ed30b (sha256:2223792564167216164843f9b2d8cc48e01bf3dc50d75e870a546efd4b9494d6; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.39755a65b6de2ed2` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l1/verification_fork.py:1469-1519 (sha256:39755a65b6de2ed282e702012573b7635bbf88037f9f3251be28775d15d6f268)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-VERIFY-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.39755a65b6de2ed2` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.2223792564167216`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l1/verification_fork.py:1662-1760 (sha256:a7edf35f45b1a0919a1662c112468df40d535ae38f70b9ed2048cd04ebfb1da5)`
### caller
- `withheld:coordinate-209f498021b8f060ae56 (sha256:644052be4d0249b2b9baa2ec81dc27d0a09a3d2fd250ff7f45c90d1ee026653c; source withheld from Open Alpha)`
### consumer
- `harness/src/towow/l1/verification_fork.py:1521-1546 (sha256:c1c3e36360281442334560e89fb3d19404d3657f91094586d227c973f85a16cb)`
### test
- `withheld:coordinate-60fe57d4987afc34198f (sha256:2223792564167216164843f9b2d8cc48e01bf3dc50d75e870a546efd4b9494d6; source withheld from Open Alpha)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l1/verification_fork.py:1469-1519 (sha256:39755a65b6de2ed282e702012573b7635bbf88037f9f3251be28775d15d6f268)`

## Drift and unresolved questions
### Applicable drift IDs
- `UNKNOWN-PHYSICAL-GATE-WIRING-001`
### Unresolved
- What physical isolation is actually enforced by the server sandbox?
- The located verify-step integration test uses replay output; it proves fail-closed consumption of a verdict, not a live independent subprocess or a real model judgment.
- No sealed execution record proves the deployed child process had a distinct context, actual tool boundary, model identity, or effective sandbox configuration from the producer it reviewed.
- No runtime corpus binds fork failures, resumes, retries, and terminal finding state transitions across a deployed review lifecycle.
- This local evolution link does not prove physical sandbox isolation in the live environment.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-VERIFY-001`
- Candidate asset node: `asset.mechanism-card.mech-verify-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
