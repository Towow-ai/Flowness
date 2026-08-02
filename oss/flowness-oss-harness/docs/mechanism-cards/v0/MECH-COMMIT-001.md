# envelope-to-gate commit or rejection (MECH-COMMIT-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `0a0b90b44b11` (superseded): The historical abandoned-candidate split is retained as superseded context for the current scan recovery node.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 1 AST-bound edge(s) and 2 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Unknown: no distinct static failure coordinate is mapped.
- **recovery boundary**: Static recovery coordinates are mapped; deployed scheduling, success and rollback behavior are Unknown.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `unknown.trigger-input`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.2fb413c098e31324`; output/consumer: `static.consumer.1.48e982cb3f433ad8`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `commit001-cli-calls-attempt`: `unknown.trigger-input` — unknown → `static.definition.1.2fb413c098e31324`; Unknown relation. Next evidence: A statically resolved CommitGate parameter binding or a sealed submit-to-gate trace. Boundary: It does not prove command invocation or acceptance.
- `commit001-attempt-calls-finalize-accept`: `static.definition.1.2fb413c098e31324` — calls → `static.consumer.1.48e982cb3f433ad8`; AST call `{'form': 'attribute', 'receiver': 'self', 'symbol': '_finalize_accept'}` at `harness/src/towow/l0/commit_gate/gate.py:489-684 (sha256:2fb413c098e31324d0495c1a5ae7416171f232cf1d7a734211bf7a8232f4278e)` targets `towow.l0.commit_gate.gate.CommitGate._finalize_accept` at `harness/src/towow/l0/commit_gate/gate.py:816-924 (sha256:48e982cb3f433ad827dbb3131756ec57b31d90f52d1f954af831c76e1df33705)` via `same_class_self`. Proves: The mapped CommitGate attempt range calls self._finalize_accept on the mapped declaration. Does not prove: It does not prove an accepted batch was committed.
- `commit001-finalize-writes-unidentified-event-state`: `static.consumer.1.48e982cb3f433ad8` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped EventLog append target and receiver identity, or a sealed commit write trace. Boundary: It does not prove event-log mutation or terminal decision at runtime.
### Objects and evidence boundary
- `static.definition.1.2fb413c098e31324` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l0/commit_gate/gate.py:489-684 (sha256:2fb413c098e31324d0495c1a5ae7416171f232cf1d7a734211bf7a8232f4278e)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.620a12d0aed524fb` (static_coordinate, caller, local_static_candidate): `withheld:coordinate-9f04dd2444e3c4fc12a1 (sha256:620a12d0aed524fb9652de3549545f7fb289930a438667f236281013a6b7d886; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.48e982cb3f433ad8` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l0/commit_gate/gate.py:816-924 (sha256:48e982cb3f433ad827dbb3131756ec57b31d90f52d1f954af831c76e1df33705)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.1225bd7c71c9fb34` (static_coordinate, test, local_static_candidate): `withheld:coordinate-5e5e7f83732569df51d6 (sha256:1225bd7c71c9fb34709fd8559dfa01e4701ebd625786ca4fd7f70bad19a534fc; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.5672d7bbec130609` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l0/commit_gate/gate.py:2092-2156 (sha256:5672d7bbec130609b85dbcea2c04d685c9223399cf8d21727c83eebb208faf5c)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-COMMIT-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.5672d7bbec130609` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.1225bd7c71c9fb34`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l0/commit_gate/gate.py:489-684 (sha256:2fb413c098e31324d0495c1a5ae7416171f232cf1d7a734211bf7a8232f4278e)`
### caller
- `withheld:coordinate-781eca34f2d033415259 (sha256:620a12d0aed524fb9652de3549545f7fb289930a438667f236281013a6b7d886; source withheld from Open Alpha)`
### consumer
- `harness/src/towow/l0/commit_gate/gate.py:816-924 (sha256:48e982cb3f433ad827dbb3131756ec57b31d90f52d1f954af831c76e1df33705)`
### test
- `withheld:coordinate-123d8fb5ab3fcfecaaee (sha256:1225bd7c71c9fb34709fd8559dfa01e4701ebd625786ca4fd7f70bad19a534fc; source withheld from Open Alpha)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l0/commit_gate/gate.py:2092-2156 (sha256:5672d7bbec130609b85dbcea2c04d685c9223399cf8d21727c83eebb208faf5c)`

## Drift and unresolved questions
### Applicable drift IDs
- None declared by the seed registry.
### Unresolved
- Which checks are active for real commits?
- No sealed server trace identifies which declared checks were active for a real commit.
- No runtime evidence proves abandoned-envelope recovery is scheduled or has fired in the deployed fleet.
- This superseded local anchor does not prove which gate checks execute for real commits.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-COMMIT-001`
- Candidate asset node: `asset.mechanism-card.mech-commit-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
