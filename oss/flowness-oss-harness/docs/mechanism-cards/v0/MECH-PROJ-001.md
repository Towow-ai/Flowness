# replayable projections and watermarks (MECH-PROJ-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `fdbf00034d4c` (superseded): Private projection repair history is declared superseded by the candidate Ledger projection node.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 0 AST-bound edge(s) and 3 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Unknown: no distinct static failure coordinate is mapped.
- **recovery boundary**: Static recovery coordinates are mapped; deployed scheduling, success and rollback behavior are Unknown.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `unknown.trigger-input`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.f619134f7ce5931a`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `proj001-test-calls-rebuild`: `unknown.trigger-input` — unknown → `static.definition.1.f619134f7ce5931a`; Unknown relation. Next evidence: A source-resolved re-export binding from flowness_ledger_core to the mapped projection function, or a direct-import test coordinate. Boundary: A test call is not production reachability.
- `proj001-rebuild-writes-unidentified-projection`: `static.definition.1.f619134f7ce5931a` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped write target identity or a sealed projection persistence trace. Boundary: It does not prove an authoritative deployment projection.
- `proj001-reader-reads-projection-bytes`: `unknown.authoritative-state` — unknown → `static.consumer.1.aaa4b750996de709`; Unknown relation. Next evidence: A mapped read_bytes target identity and receiver binding, or a sealed projection read trace. Boundary: It does not prove watermark freshness in a live process.
### Objects and evidence boundary
- `static.definition.1.f619134f7ce5931a` (static_coordinate, definition, local_static_candidate): `public-core/flowness-ledger-core/src/flowness_ledger_core/projection.py:30-66 (sha256:f619134f7ce5931aa47c51fa2b09697d60d024eb920eea22fc0f727944f57657)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.6f7b5390a36e7253` (static_coordinate, caller, local_static_candidate): `public-core/flowness-ledger-core/tests/test_projection.py:17-19 (sha256:6f7b5390a36e7253bc11fb2a96ee659a55d1f4e007990e3fdd3ecb18bb303d43)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.aaa4b750996de709` (static_coordinate, consumer, local_static_candidate): `public-core/flowness-ledger-core/src/flowness_ledger_core/projection.py:69-83 (sha256:aaa4b750996de709f0cf31410651d7e3df941c4e4caacc007bb381c3b68c4b36)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.21a47c9f1a3cb49d` (static_coordinate, test, local_static_candidate): `public-core/flowness-ledger-core/tests/test_projection.py:8-34 (sha256:21a47c9f1a3cb49dc1ca9fa8b990fbb16fd0e5d5fb3d643af2f58e5c1234016d)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.8c281dfb4fef1314` (static_coordinate, recovery, local_static_candidate): `public-core/flowness-ledger-core/tests/test_projection.py:20-25 (sha256:8c281dfb4fef13148c67558ecead52536ebb49121ea104ca8635e39a222133c8)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-PROJ-001.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.8c281dfb4fef1314` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.21a47c9f1a3cb49d`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `public-core/flowness-ledger-core/src/flowness_ledger_core/projection.py:30-66 (sha256:f619134f7ce5931aa47c51fa2b09697d60d024eb920eea22fc0f727944f57657)`
### caller
- `public-core/flowness-ledger-core/tests/test_projection.py:17-19 (sha256:6f7b5390a36e7253bc11fb2a96ee659a55d1f4e007990e3fdd3ecb18bb303d43)`
### consumer
- `public-core/flowness-ledger-core/src/flowness_ledger_core/projection.py:69-83 (sha256:aaa4b750996de709f0cf31410651d7e3df941c4e4caacc007bb381c3b68c4b36)`
### test
- `public-core/flowness-ledger-core/tests/test_projection.py:8-34 (sha256:21a47c9f1a3cb49dc1ca9fa8b990fbb16fd0e5d5fb3d643af2f58e5c1234016d)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `public-core/flowness-ledger-core/tests/test_projection.py:20-25 (sha256:8c281dfb4fef13148c67558ecead52536ebb49121ea104ca8635e39a222133c8)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-PROJECTION-CONTRACT-001`
### Unresolved
- Which reducers and consumers are complete in runtime?
- The only direct caller found in this candidate tree is a test; no runtime caller is bound.
- No sealed source snapshot or rights-cleared export binds this local tree.
- No server projection, watermark, stale-refusal or rebuild trace proves runtime use.
- Static links do not prove dynamic dispatch, authority, reachability or effect.
- This superseded local anchor does not establish semantic equivalence, active runtime reducers, watermarks, consumers, or rights.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-PROJ-001`
- Candidate asset node: `asset.mechanism-card.mech-proj-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
