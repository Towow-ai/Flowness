# stranded-worktree reconciliation (M-REFLOW-OUT-OF-BAND-RECONCILIATION)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `0feea5bb10ff` (introduced): Introduced the current stranded-worktree scanner node.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 0 AST-bound edge(s) and 2 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Unknown: no distinct static failure coordinate is mapped.
- **recovery boundary**: Static recovery coordinates are mapped; deployed scheduling, success and rollback behavior are Unknown.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `unknown.trigger-input`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.9f06aeb383975106`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `reflow001-sentinel-to-scan-unknown`: `unknown.trigger-input` — unknown → `static.definition.1.9f06aeb383975106`; Unknown relation. Next evidence: A source-range relation that directly links the mapped sentinel range to scan_stranded_worktrees, or a sealed sentinel trace. Boundary: No sentinel scan or reconciliation run is established.
- `reflow001-scan-to-finding-emission-unknown`: `static.definition.1.9f06aeb383975106` — unknown → `unknown.output-consumer`; Unknown relation. Next evidence: An auditable source relation or sealed event trace tying a scan result to the mapped finding emitter. Boundary: No reflow finding was emitted.
### Objects and evidence boundary
- `static.definition.1.9f06aeb383975106` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/reflow_reconcile.py:233-300 (sha256:9f06aeb3839751068a5084b22e6c25b8be91c8a6d9e5a0095382469c381b259a)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.fa254cd517e833d0` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l2/reflow_sentinel.py:385-425 (sha256:fa254cd517e833d0c3fab68e0fc56be30032b7c0bdbd47da965035b27b8b4388)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.925daa6b34f12629` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l2/reflow_reconcile.py:388-463 (sha256:925daa6b34f12629b8061524685c01ba7ff0a36e2b251f78683bb0ee24eb9664)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.917757dd3350ad65` (static_coordinate, test, local_static_candidate): `harness/tests/integration/test_reflow_sentinel.py:160-179 (sha256:917757dd3350ad65919fdeb4497967a6175b28679ec964bc7af588dac430a406)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.2f8293abeee62d36` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l2/reflow_reconcile.py:423-439 (sha256:2f8293abeee62d36906319ae428b5fe013a525c544c6dbef583166d46caa9dd0)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `M-REFLOW-OUT-OF-BAND-RECONCILIATION.no-distinct-static-failure-node` (no_distinct_static_failure_node): detect `unknown.failure-detection`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.2f8293abeee62d36` (static_recovery_not_bound_to_failure, local_static_candidate); negative-test locator `static.test.1.917757dd3350ad65`. Boundary: No distinct failure node was mapped in the declared static chain. This is not evidence that failure cannot occur or that the nearby recovery node owns it.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l2/reflow_reconcile.py:233-300 (sha256:9f06aeb3839751068a5084b22e6c25b8be91c8a6d9e5a0095382469c381b259a)`
### caller
- `harness/src/towow/l2/reflow_sentinel.py:385-425 (sha256:fa254cd517e833d0c3fab68e0fc56be30032b7c0bdbd47da965035b27b8b4388)`
### consumer
- `harness/src/towow/l2/reflow_reconcile.py:388-463 (sha256:925daa6b34f12629b8061524685c01ba7ff0a36e2b251f78683bb0ee24eb9664)`
### test
- `harness/tests/integration/test_reflow_sentinel.py:160-179 (sha256:917757dd3350ad65919fdeb4497967a6175b28679ec964bc7af588dac430a406)`
### failure
- Unknown: no distinct static coordinate is mapped.
### recovery
- `harness/src/towow/l2/reflow_reconcile.py:423-439 (sha256:2f8293abeee62d36906319ae428b5fe013a525c544c6dbef583166d46caa9dd0)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-DORMANT-AUTOREPAIR-001`
### Unresolved
- Is the sentinel enabled and receiving real inventory?
- No sealed source snapshot or rights-cleared public export binds this local implementation.
- No server event, timer, inventory, finding, fix, promotion, or escalation trace proves that this sentinel is enabled or has completed this path in runtime.
- The static route proves a recovery finding path and a mint-cap escalation branch, not a successful promotion or closure of every stranded worktree.
- The inventory subprocess and all deployment/timer failure modes are not represented by this excerpt chain.
- Finding emission now enters through the reflow_commit_gate adapter; this static excerpt does not prove behavioral equivalence with the former private gate or a live accepted write.
- This local evolution link does not prove sentinel enablement or real inventory input on a server.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.M-REFLOW-OUT-OF-BAND-RECONCILIATION`
- Candidate asset node: `asset.mechanism-card.m-reflow-out-of-band-reconciliation`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
