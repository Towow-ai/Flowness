# cross-process sequence and provenance preservation (MECH-EVT-002)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `d02f85b09b49` (changed): Added the sequence uniqueness write-time guard at the current static failure node.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 1 AST-bound edge(s) and 1 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Static failure coordinates are mapped; their runtime reachability and full failure coverage are Unknown.
- **recovery boundary**: Static recovery coordinates are mapped; deployed scheduling, success and rollback behavior are Unknown.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `static.caller.1.93e5d654e1c89665`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.692f7e1dc8fb0109`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `evt002-batch-enters-file-lock`: `static.caller.1.93e5d654e1c89665` — calls → `static.definition.1.692f7e1dc8fb0109`; AST call `{'form': 'attribute', 'receiver': 'self', 'symbol': '_file_lock'}` at `harness/src/towow/l0/event_log/event_log.py:674-711 (sha256:93e5d654e1c89665271ef2f386c2bfcf6d6e48ee921afdcb667ffde8b97cee61)` targets `towow.l0.event_log.event_log.EventLog._file_lock` at `harness/src/towow/l0/event_log/event_log.py:541-557 (sha256:692f7e1dc8fb0109aa4fa18914347161ea0b11366f72953f2b36ea0009e21c8b)` via `same_class_self`. Proves: The mapped batch append body calls self._file_lock on the mapped EventLog declaration. Does not prove: It does not prove cross-process lock acquisition at runtime.
- `evt002-lock-to-event-order-unknown`: `static.definition.1.692f7e1dc8fb0109` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A sealed event write/read trace that ties a lock acquisition to a sequence-bearing record. Boundary: No event ordering or lock effectiveness is established.
### Objects and evidence boundary
- `static.definition.1.692f7e1dc8fb0109` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l0/event_log/event_log.py:541-557 (sha256:692f7e1dc8fb0109aa4fa18914347161ea0b11366f72953f2b36ea0009e21c8b)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.93e5d654e1c89665` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l0/event_log/event_log.py:674-711 (sha256:93e5d654e1c89665271ef2f386c2bfcf6d6e48ee921afdcb667ffde8b97cee61)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.81c9d20e876f2255` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l0/event_log/event_log.py:904-946 (sha256:81c9d20e876f2255ab7f6685d2c06a54f89d954fe51dcfa28c0f9a79d320b03e)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.78d1303170d6e650` (static_coordinate, test, local_static_candidate): `harness/tests/unit/l0/test_event_log_concurrency.py:389-431 (sha256:78d1303170d6e650a92bbee4decca70f8459dce676574ccc88df07adadb39f41)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.failure.1.80c713284ebf2fa5` (static_coordinate, failure, local_static_candidate): `harness/src/towow/l0/event_log/event_log.py:1303-1322 (sha256:80c713284ebf2fa5f3be24c8773955f8c70a81523a07ec98a803a5b786d0fac4)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.1da608988c44a283` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l0/event_log/event_log.py:1919-1968 (sha256:1da608988c44a283209870a8cb47f7f3329006b841fb14b2613f3fe5c7fae8cd)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `MECH-EVT-002.static-failure-1` (located_static_failure_boundary): detect `static.failure.1.80c713284ebf2fa5`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.1da608988c44a283` (candidate_recovery_boundary, local_static_candidate); negative-test locator `static.test.1.78d1303170d6e650`. Boundary: Static location only; trigger, retry budget, idempotence, and runtime outcome remain Unknown.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l0/event_log/event_log.py:541-557 (sha256:692f7e1dc8fb0109aa4fa18914347161ea0b11366f72953f2b36ea0009e21c8b)`
### caller
- `harness/src/towow/l0/event_log/event_log.py:674-711 (sha256:93e5d654e1c89665271ef2f386c2bfcf6d6e48ee921afdcb667ffde8b97cee61)`
### consumer
- `harness/src/towow/l0/event_log/event_log.py:904-946 (sha256:81c9d20e876f2255ab7f6685d2c06a54f89d954fe51dcfa28c0f9a79d320b03e)`
### test
- `harness/tests/unit/l0/test_event_log_concurrency.py:389-431 (sha256:78d1303170d6e650a92bbee4decca70f8459dce676574ccc88df07adadb39f41)`
### failure
- `harness/src/towow/l0/event_log/event_log.py:1303-1322 (sha256:80c713284ebf2fa5f3be24c8773955f8c70a81523a07ec98a803a5b786d0fac4)`
### recovery
- `harness/src/towow/l0/event_log/event_log.py:1919-1968 (sha256:1da608988c44a283209870a8cb47f7f3329006b841fb14b2613f3fe5c7fae8cd)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-EVENT-001`
- `DRIFT-STUB-BOUNDARY-001`
### Unresolved
- Which raw-port producers exist in the sealed runtime?
- No sealed deployed EventLog snapshot or runtime sequence trace establishes which writer processes actually share this lock.
- segments.append_raw_event_line remains an explicitly weaker raw append port; its deployed producers and post-hoc audit coverage need a sealed producer-to-audit trace.
- The local multiprocess test proves this checkout's file-lock behavior, not cross-host, network filesystem, or deployed recovery behavior.
- This local evolution link does not identify live raw-port producers or prove runtime provenance preservation.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.MECH-EVT-002`
- Candidate asset node: `asset.mechanism-card.mech-evt-002`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
