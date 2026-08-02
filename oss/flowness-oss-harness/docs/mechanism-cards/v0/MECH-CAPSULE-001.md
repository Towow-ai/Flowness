# provenance-bound capsule context (MECH-CAPSULE-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `ec700a8e2322` (changed): Changed the current capsule assembly function to carry session provenance.
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
- Trigger/input object: `static.caller.1.1d890bdac0496d6c`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.c02c081796f7c292`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `capsule001-audit-calls-assemble`: `static.caller.1.1d890bdac0496d6c` — calls → `static.definition.1.c02c081796f7c292`; AST call `{'form': 'name', 'symbol': 'assemble_capsule'}` at `withheld:coordinate-2f87d7f2400b451060ca (sha256:1d890bdac0496d6ccddf978f6ee73c70ba0c0329374eedff7f32feb616dd7d92; source withheld from Open Alpha)` targets `towow.l0.capsule.pipeline.assemble_capsule` at `harness/src/towow/l0/capsule/pipeline.py:229-305 (sha256:c02c081796f7c2920e06976b72a10f75cb48687e4ffcf107fd65f17a82b2299e)` via `direct_import_name`. Proves: The audit helper directly imports and calls the mapped capsule assembly declaration. Does not prove: It does not prove a capsule was emitted in production.
- `capsule001-compiled-event-write-segment`: `static.consumer.1.b5f719ddc698c5ea` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped _emit_capsule_compiled target and EventLog target identity, or a sealed capsule emission trace. Boundary: The definition-to-emission control flow, EventLog write, and consumer delivery are not proved by this segment alone.
### Objects and evidence boundary
- `static.definition.1.c02c081796f7c292` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l0/capsule/pipeline.py:229-305 (sha256:c02c081796f7c2920e06976b72a10f75cb48687e4ffcf107fd65f17a82b2299e)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.1d890bdac0496d6c` (static_coordinate, caller, local_static_candidate): `withheld:coordinate-2f87d7f2400b451060ca (sha256:1d890bdac0496d6ccddf978f6ee73c70ba0c0329374eedff7f32feb616dd7d92; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.b5f719ddc698c5ea` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l0/capsule/pipeline.py:610-694 (sha256:b5f719ddc698c5ea5bc6f47895b78dd4cccd12d25dc1c181a4d5256db5fd6346)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.6af067e5ef599e16` (static_coordinate, test, local_static_candidate): `withheld:coordinate-61c6c91a21b359bb7d1b (sha256:6af067e5ef599e16f5c780d7ba2910364fd63fffede31c4916c39c94ca73c6b8; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.5f171f66ca89f1d9` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l0/capsule/pipeline.py:1900-1949 (sha256:5f171f66ca89f1d91a1934f8268189a347fea7c66c56e0e9b9f8b4363425d2c7)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-CAPSULE-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.5f171f66ca89f1d9` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.6af067e5ef599e16`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l0/capsule/pipeline.py:229-305 (sha256:c02c081796f7c2920e06976b72a10f75cb48687e4ffcf107fd65f17a82b2299e)`
### caller
- `withheld:coordinate-64ddca111cbda6b40697 (sha256:1d890bdac0496d6ccddf978f6ee73c70ba0c0329374eedff7f32feb616dd7d92; source withheld from Open Alpha)`
### consumer
- `harness/src/towow/l0/capsule/pipeline.py:610-694 (sha256:b5f719ddc698c5ea5bc6f47895b78dd4cccd12d25dc1c181a4d5256db5fd6346)`
### test
- `withheld:coordinate-9f449c8b13379df7514d (sha256:6af067e5ef599e16f5c780d7ba2910364fd63fffede31c4916c39c94ca73c6b8; source withheld from Open Alpha)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l0/capsule/pipeline.py:1900-1949 (sha256:5f171f66ca89f1d91a1934f8268189a347fea7c66c56e0e9b9f8b4363425d2c7)`

## Drift and unresolved questions
### Applicable drift IDs
- None declared by the seed registry.
### Unresolved
- Which live prompt consumer receives a capsule?
- No sealed server capsule plus downstream prompt-consumer attribution chain is available.
- The static test proves an isolated local event-log flow, not a deployed worker's context delivery.
- This local evolution link does not prove a server prompt consumer received the capsule.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-CAPSULE-001`
- Candidate asset node: `asset.mechanism-card.mech-capsule-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
