# forward provenance anchor (MECH-PROV-001)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `d5e4c5aa6c22` (introduced): Introduced the current forward provenance-anchor builder.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 1 AST-bound edge(s) and 1 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Static failure coordinates are mapped; their runtime reachability and full failure coverage are Unknown.
- **recovery boundary**: Unknown: no distinct static recovery coordinate is mapped.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `unknown.trigger-input`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.c135d59aee143549`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `prov001-emitter-builds-anchor-intent`: `static.caller.1.b847b0ec874f37ff` — helper_calls → `static.definition.1.c135d59aee143549`; AST call `{'form': 'name', 'symbol': 'build_provenance_anchor_intent'}` at `harness/src/towow/l0/event_log/provenance_anchor.py:101-139 (sha256:b847b0ec874f37ffd9929242618b612379e1e11c0a2d0ee361259182e80872be)` targets `towow.l0.event_log.provenance_anchor.build_provenance_anchor_intent` at `harness/src/towow/l0/event_log/provenance_anchor.py:41-99 (sha256:c135d59aee143549417ab181761f08688ab5b9a4f706e5335a84239fbfa30e70)` via `same_module_name`. Proves: The emitter calls the mapped module-level provenance intent builder. Does not prove: No non-test source caller of this emitter was found, so it does not prove a production trigger or persisted anchor.
- `prov001-emitter-writes-unidentified-event-state`: `static.caller.1.b847b0ec874f37ff` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A mapped EventLog.write_direct target and event-log receiver identity, or a sealed provenance write trace. Boundary: It does not establish committed provenance or a consumer.
### Objects and evidence boundary
- `static.definition.1.c135d59aee143549` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l0/event_log/provenance_anchor.py:41-99 (sha256:c135d59aee143549417ab181761f08688ab5b9a4f706e5335a84239fbfa30e70)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.b847b0ec874f37ff` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l0/event_log/provenance_anchor.py:101-139 (sha256:b847b0ec874f37ffd9929242618b612379e1e11c0a2d0ee361259182e80872be)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.08cabc356d2c95ef` (static_coordinate, consumer, local_static_candidate): `withheld:coordinate-ddd55fffb431dfea2409 (sha256:08cabc356d2c95ef6d089044755ad462fefe36cfe890726e5564e10c6753de2f; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.f964bf6afdfda5e0` (static_coordinate, test, local_static_candidate): `withheld:coordinate-f79eb1370733eeb6cedc (sha256:f964bf6afdfda5e07bbebaecc2a7a3f2405c73bf5abf48fff8bb34c4d4bce9cc; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.failure.1.f49d940b6d91cd21` (static_coordinate, failure, local_static_candidate): `harness/src/towow/l0/event_log/provenance_anchor.py:56-58 (sha256:f49d940b6d91cd21f70e948c45a668f930c79cc6f91b1559890032e9dab01b01)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-PROV-001.static-failure-1` (located_static_failure_boundary): detect `static.failure.1.f49d940b6d91cd21`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `unknown.failure-handoff` (recovery_owner_unknown, unknown); negative-test locator `static.test.1.f964bf6afdfda5e0`. Boundary: Static location only; trigger, retry budget, idempotence, and runtime outcome remain Unknown.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l0/event_log/provenance_anchor.py:41-99 (sha256:c135d59aee143549417ab181761f08688ab5b9a4f706e5335a84239fbfa30e70)`
### caller
- `harness/src/towow/l0/event_log/provenance_anchor.py:101-139 (sha256:b847b0ec874f37ffd9929242618b612379e1e11c0a2d0ee361259182e80872be)`
### consumer
- `withheld:coordinate-ac885b190f7365eee633 (sha256:08cabc356d2c95ef6d089044755ad462fefe36cfe890726e5564e10c6753de2f; source withheld from Open Alpha)`
### test
- `withheld:coordinate-c2b3db52b165cc340faa (sha256:f964bf6afdfda5e07bbebaecc2a7a3f2405c73bf5abf48fff8bb34c4d4bce9cc; source withheld from Open Alpha)`
### failure
- `harness/src/towow/l0/event_log/provenance_anchor.py:56-58 (sha256:f49d940b6d91cd21f70e948c45a668f930c79cc6f91b1559890032e9dab01b01)`
### recovery
- Unknown: no distinct static coordinate is mapped.

## Drift and unresolved questions
### Applicable drift IDs
- None declared by the seed registry.
### Unresolved
- Which production writers create and consume anchors?
- The only located source caller is the helper itself; no production writer callsite was found in this local tree.
- No sealed production evidence identifies a continuously-running anchor producer or consumer.
- This local evolution link does not identify production writers or consumers of provenance anchors.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-PROV-001`
- Candidate asset node: `asset.mechanism-card.mech-prov-001`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
