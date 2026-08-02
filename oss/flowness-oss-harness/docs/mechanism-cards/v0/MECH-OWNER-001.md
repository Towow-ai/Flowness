# owner-answer reflow (MECH-OWNER-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `2880d73c8bda` (changed): Changed the current owner-answer delivery function.
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
- Trigger/input object: `static.caller.1.b44d4553f3eba3e2`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.4dad4d431c2e3d84`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `owner001-orchestrator-calls-delivery`: `static.caller.1.b44d4553f3eba3e2` — calls → `static.definition.1.4dad4d431c2e3d84`; AST call `{'form': 'attribute', 'receiver': 'escalation_reflow', 'symbol': 'deliver_goal_answer'}` at `harness/src/towow/l2/orchestrator.py:8376-8423 (sha256:b44d4553f3eba3e21075ec15c1abea9a02ab16461641de1f12aa90a06470c88a)` targets `towow.l2.escalation_reflow.deliver_goal_answer` at `harness/src/towow/l2/escalation_reflow.py:279-310 (sha256:4dad4d431c2e3d84fecdcb90d2c47a2aea00e02a8d47069a7060939cec55264a)` via `imported_module_attribute`. Proves: The mapped orchestrator range imports escalation_reflow and calls its mapped delivery declaration. Does not prove: It does not prove delivery or owner authority.
- `owner001-delivery-to-reflow-consumer-unknown`: `static.definition.1.4dad4d431c2e3d84` — unknown → `unknown.output-consumer`; Unknown relation. Next evidence: A source relation or sealed reflow trace connecting delivery to the mapped orchestrator consumer section. Boundary: No reflow result or owner decision is established.
### Objects and evidence boundary
- `static.definition.1.4dad4d431c2e3d84` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/escalation_reflow.py:279-310 (sha256:4dad4d431c2e3d84fecdcb90d2c47a2aea00e02a8d47069a7060939cec55264a)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.b44d4553f3eba3e2` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l2/orchestrator.py:8376-8423 (sha256:b44d4553f3eba3e21075ec15c1abea9a02ab16461641de1f12aa90a06470c88a)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.304b37fb0d48cc98` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l2/orchestrator.py:8424-8453 (sha256:304b37fb0d48cc982f28b9ebfaafaafb989cdf0666bec34d1ba30cb449aed4e3)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.187ffa2b6089ad49` (static_coordinate, test, local_static_candidate): `withheld:coordinate-7533e710d50114468411 (sha256:187ffa2b6089ad49c592d929addc07bb1c44d6ef3f20e6eb3cdc637a8d00af20; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.16371cace80724b7` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l2/orchestrator.py:8455-8477 (sha256:16371cace80724b71f94752d444649f7f1d9121603c09da343f82b7bb5aa8d90)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-OWNER-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.16371cace80724b7` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.187ffa2b6089ad49`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l2/escalation_reflow.py:279-310 (sha256:4dad4d431c2e3d84fecdcb90d2c47a2aea00e02a8d47069a7060939cec55264a)`
### caller
- `harness/src/towow/l2/orchestrator.py:8376-8423 (sha256:b44d4553f3eba3e21075ec15c1abea9a02ab16461641de1f12aa90a06470c88a)`
### consumer
- `harness/src/towow/l2/orchestrator.py:8424-8453 (sha256:304b37fb0d48cc982f28b9ebfaafaafb989cdf0666bec34d1ba30cb449aed4e3)`
### test
- `withheld:coordinate-561ccf514afd1148be8e (sha256:187ffa2b6089ad49c592d929addc07bb1c44d6ef3f20e6eb3cdc637a8d00af20; source withheld from Open Alpha)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l2/orchestrator.py:8455-8477 (sha256:16371cace80724b71f94752d444649f7f1d9121603c09da343f82b7bb5aa8d90)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-PRIVATE-RUNTIME-002`
### Unresolved
- Can a server trace prove PTY delivery and canonical answer events?
- No sealed server trace binds an owner answer, its canonical event, the target goal session, PTY injection, and a confirmed transcript turn into one provenance chain.
- The local test covers the conservative unresolvable-session path; it does not prove a live parked session received a real answer or resumed useful work.
- The current escalation inbox can display resolved from a matching owner judgment before delivery is proven; that display-level false-closure drift must remain visible in any public explanation.
- This local evolution link does not prove PTY delivery, transcript turn, or canonical answer events on a server.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-OWNER-001`
- Candidate asset node: `asset.mechanism-card.mech-owner-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
