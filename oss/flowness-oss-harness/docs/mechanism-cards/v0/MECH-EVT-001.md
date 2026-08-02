# committed-visible batch ledger (MECH-EVT-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `3aaf56904011` (superseded): Legacy physical batch writer was replaced by the bounded Ledger candidate append node.
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
- Trigger/input object: `static.caller.1.481448aea6ebdfe5`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.b00653c9064f8517`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `evt001-proposal-calls-append`: `static.caller.1.481448aea6ebdfe5` — calls → `static.definition.1.b00653c9064f8517`; AST call `{'form': 'attribute', 'receiver': 'self', 'symbol': '_append'}` at `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:179-220 (sha256:481448aea6ebdfe5a19e45ee798a0a8f88f099f90137dce479e198df0b7f42a9)` targets `flowness_ledger_core.ledger.Ledger._append` at `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:151-175 (sha256:b00653c9064f8517cf498410c4ebb41493fd661728beb018f0d31e6510f0bff6)` via `same_class_self`. Proves: begin_proposal contains self._append calls whose target is the mapped Ledger._append declaration. Does not prove: It does not prove that any call completed at runtime.
- `evt001-append-writes-unidentified-ledger`: `static.definition.1.b00653c9064f8517` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A sealed writer-to-ledger trace or a mapped handle/write target identity. Boundary: It does not identify the authoritative runtime ledger or prove durability.
- `evt001-reader-loads-unidentified-ledger`: `unknown.authoritative-state` — unknown → `static.consumer.1.cd27b1744ba0cafa`; Unknown relation. Next evidence: A mapped _load declaration plus a source-resolved receiver binding, or a sealed read trace. Boundary: It does not establish that a read observes a particular append.
### Objects and evidence boundary
- `static.definition.1.b00653c9064f8517` (static_coordinate, definition, local_static_candidate): `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:151-175 (sha256:b00653c9064f8517cf498410c4ebb41493fd661728beb018f0d31e6510f0bff6)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.481448aea6ebdfe5` (static_coordinate, caller, local_static_candidate): `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:179-220 (sha256:481448aea6ebdfe5a19e45ee798a0a8f88f099f90137dce479e198df0b7f42a9)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.cd27b1744ba0cafa` (static_coordinate, consumer, local_static_candidate): `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:222-232 (sha256:cd27b1744ba0cafa8e1b169eaa03b08b010ef959ab8a34f20a5dd20186efe279)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.b18882149a354b40` (static_coordinate, test, local_static_candidate): `public-core/flowness-ledger-core/tests/test_ledger.py:12-69 (sha256:b18882149a354b4090d485c3730e3f1f86f8809174e3ce0499f425b9a72167c0)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.d631d0c6a61fc021` (static_coordinate, recovery, local_static_candidate): `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:240-276 (sha256:d631d0c6a61fc021a0d041d832dd976777e0ef9c13b23d3259a625badd7d6842)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-EVT-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.d631d0c6a61fc021` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.b18882149a354b40`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:151-175 (sha256:b00653c9064f8517cf498410c4ebb41493fd661728beb018f0d31e6510f0bff6)`
### caller
- `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:179-220 (sha256:481448aea6ebdfe5a19e45ee798a0a8f88f099f90137dce479e198df0b7f42a9)`
### consumer
- `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:222-232 (sha256:cd27b1744ba0cafa8e1b169eaa03b08b010ef959ab8a34f20a5dd20186efe279)`
### test
- `public-core/flowness-ledger-core/tests/test_ledger.py:12-69 (sha256:b18882149a354b4090d485c3730e3f1f86f8809174e3ce0499f425b9a72167c0)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:240-276 (sha256:d631d0c6a61fc021a0d041d832dd976777e0ef9c13b23d3259a625badd7d6842)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-PUBLIC-EXPORT-004`
### Unresolved
- Which deployed writers and recovery traces use this protocol?
- No sealed source snapshot or rights-cleared export binds this local tree.
- No server writer, consumer, delivery or recovery trace proves runtime use.
- Static links do not prove dynamic dispatch, authority, reachability or effect.
- This superseded local anchor does not establish semantic equivalence between the private EventLog and Ledger candidate, runtime behavior, or rights.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.commit-visible-ledger`
- Candidate asset node: `asset.mechanism-card.mech-evt-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
