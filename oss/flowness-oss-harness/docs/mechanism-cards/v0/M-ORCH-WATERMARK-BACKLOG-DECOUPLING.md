# capacity-safe watermark advance (M-ORCH-WATERMARK-BACKLOG-DECOUPLING)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `c684684bdd51` (changed): Changed the current watermark persistence function used by the static chain.
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
- Trigger/input object: `static.caller.1.83d34abec60413a5`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.f40da93787c977fb`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `watermark001-loop-calls-save`: `static.caller.1.83d34abec60413a5` — calls → `static.definition.1.f40da93787c977fb`; AST call `{'form': 'name', 'symbol': 'save_watermark_atomic'}` at `harness/src/towow/l2/orchestrator.py:11444-11453 (sha256:83d34abec60413a5eac952b0d655e424440be73a61e88cad64425f977454bbff)` targets `towow.l2.orchestrator.save_watermark_atomic` at `harness/src/towow/l2/orchestrator.py:1362-1367 (sha256:f40da93787c977fb28d948add4aedb3184875252d13159c4f7960d9b8bd2cbd1)` via `same_module_name`. Proves: The polling-loop tail calls the mapped module-level save_watermark_atomic declaration. Does not prove: It does not prove a durable or capacity-safe advance.
- `watermark001-save-writes-unidentified-watermark`: `static.definition.1.f40da93787c977fb` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped write_text target and watermark path identity, or a sealed watermark recovery trace. Boundary: It does not identify a deployed watermark or a backlog recovery consumer.
### Objects and evidence boundary
- `static.definition.1.f40da93787c977fb` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/orchestrator.py:1362-1367 (sha256:f40da93787c977fb28d948add4aedb3184875252d13159c4f7960d9b8bd2cbd1)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.83d34abec60413a5` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l2/orchestrator.py:11444-11453 (sha256:83d34abec60413a5eac952b0d655e424440be73a61e88cad64425f977454bbff)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.bbed4bfd51c7f575` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l2/orchestrator.py:10877-10882 (sha256:bbed4bfd51c7f575cb9f4cd6262b26607a9e1fcca68873e580a7c91614f0b194)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.1ca2f41fc38e9f44` (static_coordinate, test, local_static_candidate): `withheld:coordinate-1e84a055ca7412d09377 (sha256:1ca2f41fc38e9f443baa75a487f92c8868853f2ec600b205619f7cbc144a1745; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.e234ca0af7fdafb3` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l2/orchestrator.py:3885-3920 (sha256:e234ca0af7fdafb3b1c333d0b00f1c09aaca89865dfe5e152d1aef12951e9b72)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `M-ORCH-WATERMARK-BACKLOG-DECOUPLING.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.e234ca0af7fdafb3` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.1ca2f41fc38e9f44`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l2/orchestrator.py:1362-1367 (sha256:f40da93787c977fb28d948add4aedb3184875252d13159c4f7960d9b8bd2cbd1)`
### caller
- `harness/src/towow/l2/orchestrator.py:11444-11453 (sha256:83d34abec60413a5eac952b0d655e424440be73a61e88cad64425f977454bbff)`
### consumer
- `harness/src/towow/l2/orchestrator.py:10877-10882 (sha256:bbed4bfd51c7f575cb9f4cd6262b26607a9e1fcca68873e580a7c91614f0b194)`
### test
- `withheld:coordinate-24ceb13501ec49fedb87 (sha256:1ca2f41fc38e9f443baa75a487f92c8868853f2ec600b205619f7cbc144a1745; source withheld from Open Alpha)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l2/orchestrator.py:3885-3920 (sha256:e234ca0af7fdafb3b1c333d0b00f1c09aaca89865dfe5e152d1aef12951e9b72)`

## Drift and unresolved questions
### Applicable drift IDs
- None declared by the seed registry.
### Unresolved
- What real backlog and capacity evidence exists?
- No sealed source snapshot binds this local implementation to a rights-cleared public export.
- No runtime backlog, capacity, crash-recovery, or watermark telemetry establishes production behavior or load limits.
- The mapped recovery only covers non-execution backlog markers; completeness of all truncation and retry paths is not proven by this static chain.
- This local evolution link does not prove current backlog volume, governor settings, or server behavior.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.M-ORCH-WATERMARK-BACKLOG-DECOUPLING`
- Candidate asset node: `asset.mechanism-card.m-orch-watermark-backlog-decoupling`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
