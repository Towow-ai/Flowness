# per-key spawn claim fencing (M-ORCH-SINGLE-EXEC-CLAIM-FENCING)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `77a3e85fe33b` (superseded): The stale-reaper history is declared superseded rather than asserted as the current claim-spawn implementation.
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
- Trigger/input object: `static.caller.1.4828088991026e3f`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.947dfae13ae6d2f9`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `claim001-spawn-calls-claim`: `static.caller.1.4828088991026e3f` — calls → `static.definition.1.947dfae13ae6d2f9`; AST call `{'form': 'name', 'symbol': 'claim_exec_spawn'}` at `harness/src/towow/l2/orchestrator.py:9399-9450 (sha256:4828088991026e3f3c281afd6717b0f3b1a2fd4b33bf4718074b9bfd9804dec4)` targets `towow.l2.orchestrator.claim_exec_spawn` at `harness/src/towow/l2/orchestrator.py:1522-1533 (sha256:947dfae13ae6d2f9f45d448e231251206231e68222c3a2fe251be0950ebfd7eb)` via `same_module_name`. Proves: The spawn path calls the mapped module-level claim_exec_spawn declaration. Does not prove: It does not prove that a claim was acquired.
- `claim001-wrapper-calls-claim-task`: `static.definition.1.947dfae13ae6d2f9` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped claim_task target identity and claim-store evidence, or a sealed fencing trace. Boundary: The claim-store identity and fencing effectiveness remain unknown.
### Objects and evidence boundary
- `static.definition.1.947dfae13ae6d2f9` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/orchestrator.py:1522-1533 (sha256:947dfae13ae6d2f9f45d448e231251206231e68222c3a2fe251be0950ebfd7eb)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.4828088991026e3f` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l2/orchestrator.py:9399-9450 (sha256:4828088991026e3f3c281afd6717b0f3b1a2fd4b33bf4718074b9bfd9804dec4)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.c40cc0fea1d5d47c` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l0/commit_gate/fencing_check.py:74-141 (sha256:c40cc0fea1d5d47c4d3e5b7a42467ed0b82fa8a341cf14e0f10b85a2b128774a)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.7fa480fba26c34a8` (static_coordinate, test, local_static_candidate): `withheld:coordinate-4c413c237907471f305f (sha256:7fa480fba26c34a8887ac3ee6011a00b4e781ee558036154477e0c4496c2cd2c; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.ade9546eee3f66cb` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l2/orchestrator.py:4126-4154 (sha256:ade9546eee3f66cb818764ddc7452118fed52548ff304796c74cd0d22c530c40)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `M-ORCH-SINGLE-EXEC-CLAIM-FENCING.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.ade9546eee3f66cb` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.7fa480fba26c34a8`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l2/orchestrator.py:1522-1533 (sha256:947dfae13ae6d2f9f45d448e231251206231e68222c3a2fe251be0950ebfd7eb)`
### caller
- `harness/src/towow/l2/orchestrator.py:9399-9450 (sha256:4828088991026e3f3c281afd6717b0f3b1a2fd4b33bf4718074b9bfd9804dec4)`
### consumer
- `harness/src/towow/l0/commit_gate/fencing_check.py:74-141 (sha256:c40cc0fea1d5d47c4d3e5b7a42467ed0b82fa8a341cf14e0f10b85a2b128774a)`
### test
- `withheld:coordinate-22bfbf0f933df40c4ade (sha256:7fa480fba26c34a8887ac3ee6011a00b4e781ee558036154477e0c4496c2cd2c; source withheld from Open Alpha)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l2/orchestrator.py:4126-4154 (sha256:ade9546eee3f66cb818764ddc7452118fed52548ff304796c74cd0d22c530c40)`

## Drift and unresolved questions
### Applicable drift IDs
- None declared by the seed registry.
### Unresolved
- Do all dispatch paths use the same fence?
- No sealed source snapshot binds this local implementation to a rights-cleared public export.
- No runtime evidence proves every execution dispatch path enters this fence before spawning.
- The source permits an explicit enforce-off mode; deployed enforcement state and cross-process filesystem assumptions remain unverified.
- This superseded local anchor does not prove all dispatch paths share the same live fence.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.M-ORCH-SINGLE-EXEC-CLAIM-FENCING`
- Candidate asset node: `asset.mechanism-card.m-orch-single-exec-claim-fencing`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
