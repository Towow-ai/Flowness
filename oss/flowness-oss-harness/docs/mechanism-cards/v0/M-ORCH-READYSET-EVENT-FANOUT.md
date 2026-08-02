# event-sourced ready-set fan-out (M-ORCH-READYSET-EVENT-FANOUT)

Status: **experimental**; ceiling: **candidate_mapped_only**.
This is an unsealed local static/history card. It is not a runtime, rights, or public-release assertion.

## Why it exists (local history only)
- `88a1bdc50d30` (superseded): The historical single-flight hunk is retained as superseded context for the present ready-set node.
- Boundary: History anchors are evolution clues only; they do not prove current semantics, runtime use, or rights.

## State, authority, and behavior boundary
- **trigger and input**: Static caller coordinates are mapped; the live trigger condition, input contract and reachability are Unknown.
- **output and consumer**: Static consumer coordinates are mapped; delivered output and live consumer behavior are Unknown.
- **authoritative state**: Unknown: local static excerpts do not establish the authoritative runtime state or store.
- **state transitions**: Manual source-relation review maps 5 AST-bound edge(s) and 4 explicit Unknown edge(s); runtime ordering, authoritative mutation and terminal paths remain Unknown.
- **authority and permission**: Unknown: local static excerpts do not establish effective runtime authority, permission checks, or owner gates.
- **failure boundary**: Static failure coordinates are mapped; their runtime reachability and full failure coverage are Unknown.
- **recovery boundary**: Static recovery coordinates are mapped; deployed scheduling, success and rollback behavior are Unknown.

## Mechanism-level local semantic contract
- Contract status: **local_static_candidate**; runtime status: **unknown**.
- Trigger/input object: `static.caller.1.e6dded9d686c873f`; authoritative state: `unknown.authoritative-state`; transition: `static.definition.1.9c65564aba1a8501`; output/consumer: `unknown.output-consumer`; terminal policy: `unknown.terminal-policy`.
### Reviewed static relation edges
- `readyset001-router-calls-ready-decision`: `static.caller.1.e6dded9d686c873f` — calls → `static.definition.1.9c65564aba1a8501`; AST call `{'form': 'attribute', 'receiver': 'self', 'symbol': '_ready_execution_decisions'}` at `harness/src/towow/l2/orchestrator.py:930-936 (sha256:e6dded9d686c873fa44504def22bc5aa9437630839c3ecd2c387194a5c02d28d)` targets `towow.l2.orchestrator.OrchestratorDaemon._ready_execution_decisions` at `harness/src/towow/l2/orchestrator.py:1049-1103 (sha256:9c65564aba1a85017f4f7484764970faf1b606f75adda6f8db0ed48374ed3eeb)` via `same_class_self`. Proves: The mapped router range calls self._ready_execution_decisions on the mapped OrchestratorDaemon declaration. Does not prove: It does not prove a dispatch was made.
- `readyset001-decision-to-dispatch-consumer-unknown`: `static.definition.1.9c65564aba1a8501` — unknown → `unknown.output-consumer`; Unknown relation. Next evidence: A source-resolved value-flow from the returned DispatchDecision list through daemon.decisions and exec_batch, or a sealed dispatch trace with the same trigger_event_id and task_id. Boundary: No particular ready decision is proved to reach a spawn or execution result.
- `readyset001-poll-loop-calls-execution-batch`: `static.consumer.2.8dd8397d93fd6c03` — calls_batch_dispatcher → `static.recovery.1.2cdda8966cc74a48`; AST call `{'form': 'name', 'symbol': '_dispatch_execution_batch'}` at `harness/src/towow/l2/orchestrator.py:11359-11372 (sha256:8dd8397d93fd6c031bf6217c55f35b8cc645fdcb408df16f18162d8d9d8c9144)` targets `towow.l2.orchestrator._dispatch_execution_batch` at `harness/src/towow/l2/orchestrator.py:9546-9624 (sha256:2cdda8966cc74a4886a75081a2de87bbc73940b3dc012d44657f8a3f2f7030b7)` via `same_module_name`. Proves: The mapped polling-loop consumer calls the mapped execution batch dispatcher with exec_batch and the local dispatch controls. Does not prove: It does not prove the loop is running, exec_batch contains a particular ready decision, or a spawn occurs.
- `readyset001-batch-calls-backlog-ready-recompute`: `static.recovery.1.2cdda8966cc74a48` — calls_backlog_recompute → `static.recovery.2.2626f44164c0fe20`; AST call `{'form': 'name', 'symbol': 'ready_execution_tasks_to_dispatch'}` at `harness/src/towow/l2/orchestrator.py:9546-9624 (sha256:2cdda8966cc74a4886a75081a2de87bbc73940b3dc012d44657f8a3f2f7030b7)` targets `towow.l2.execution_dispatch.ready_execution_tasks_to_dispatch` at `harness/src/towow/l2/execution_dispatch.py:330-358 (sha256:2626f44164c0fe20a5895b0025493443e09892569d26a122909deeb62b0b45eb)` via `direct_import_name`. Proves: The mapped batch dispatcher directly imports and calls the mapped ready-execution recompute while rebuilding its per-plan candidate pool. Does not prove: It does not prove a failed task is rediscovered, selected, or successfully re-spawned at runtime.
- `readyset001-candidate-state-authority-unknown`: `static.recovery.1.2cdda8966cc74a48` — unknown → `unknown.authoritative-state`; Unknown relation. Next evidence: A sealed observation that binds the committed event snapshot, dispatch stamps, candidate pool and selected batch to the deployed scheduler's authoritative readback. Boundary: The in-memory pool, source event snapshot or local stamp directory is the authoritative deployed scheduling state.
- `readyset001-failure-branch-calls-failure-event`: `static.failure.1.afee38f6ae4dad51` — calls_failure_event_emitter → `static.definition.3.2e6664077288999e`; AST call `{'form': 'name', 'symbol': 'emit_orchestrator_dispatch_failed'}` at `harness/src/towow/l2/orchestrator.py:9453-9543 (sha256:afee38f6ae4dad514d2816c66d614401a9d3a6fa6d4ce4abdbf32150483d24a2)` targets `towow.l2.orchestrator.emit_orchestrator_dispatch_failed` at `harness/src/towow/l2/orchestrator.py:4006-4052 (sha256:2e6664077288999ec928f322bd0196b7eae2b5c041822c5a0039d03559dc1d8e)` via `same_module_name`. Proves: The mapped failed-spawn branch calls the mapped typed OrchestratorDispatchFailed emitter. Does not prove: It does not prove the failure branch was reached or the event was durably observed by a deployed consumer.
- `readyset001-failure-branch-calls-owner-notification`: `static.failure.1.afee38f6ae4dad51` — calls_notification_emitter → `static.definition.2.93f3bba0490cf994`; AST call `{'form': 'name', 'symbol': 'emit_orchestrator_dispatched'}` at `harness/src/towow/l2/orchestrator.py:9453-9543 (sha256:afee38f6ae4dad514d2816c66d614401a9d3a6fa6d4ce4abdbf32150483d24a2)` targets `towow.l2.orchestrator.emit_orchestrator_dispatched` at `harness/src/towow/l2/orchestrator.py:3977-4003 (sha256:93f3bba0490cf994810d5616eb8cb43fb7a7f55b0d532cbfc6ef1075e6980d75)` via `same_module_name`. Proves: The mapped failed-spawn branch constructs a main-inbound DispatchDecision and calls the mapped dispatch-event emitter. Does not prove: It does not prove owner-visible delivery, acknowledgement or remediation.
- `readyset001-failure-to-recovery-handoff-unknown`: `static.failure.1.afee38f6ae4dad51` — unknown → `unknown.failure-handoff`; Unknown relation. Next evidence: A sealed failed-spawn trace followed by a later ready-set recompute and redispatch sharing the same task_id, or a source-resolved state-flow from omitted success stamp to pool eligibility. Boundary: Omitting the success stamp caused a later redispatch, or that notification and reflow completed.
- `readyset001-unknown-handoff-to-backlog-reentry`: `unknown.failure-handoff` — unknown → `static.recovery.1.2cdda8966cc74a48`; Unknown relation. Next evidence: A trace/readback proving that the failed task is absent from the dispatch stamp set, reappears in the batch candidate pool, and is selected without violating the claim fence. Boundary: A failed or capped task actually re-entered the ready set or was safely re-dispatched.
### Objects and evidence boundary
- `static.definition.1.9c65564aba1a8501` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/orchestrator.py:1049-1103 (sha256:9c65564aba1a85017f4f7484764970faf1b606f75adda6f8db0ed48374ed3eeb)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.definition.2.93f3bba0490cf994` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/orchestrator.py:3977-4003 (sha256:93f3bba0490cf994810d5616eb8cb43fb7a7f55b0d532cbfc6ef1075e6980d75)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.definition.3.2e6664077288999e` (static_coordinate, definition, local_static_candidate): `harness/src/towow/l2/orchestrator.py:4006-4052 (sha256:2e6664077288999ec928f322bd0196b7eae2b5c041822c5a0039d03559dc1d8e)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.caller.1.e6dded9d686c873f` (static_coordinate, caller, local_static_candidate): `harness/src/towow/l2/orchestrator.py:930-936 (sha256:e6dded9d686c873fa44504def22bc5aa9437630839c3ecd2c387194a5c02d28d)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.1.797f81fee6afc7ea` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l2/orchestrator.py:10969-10981 (sha256:797f81fee6afc7ea55e6d486ad9780285d2f506180c90c6bb6cee12da3858bc1)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.consumer.2.8dd8397d93fd6c03` (static_coordinate, consumer, local_static_candidate): `harness/src/towow/l2/orchestrator.py:11359-11372 (sha256:8dd8397d93fd6c031bf6217c55f35b8cc645fdcb408df16f18162d8d9d8c9144)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.1.41f334f6b73d4e43` (static_coordinate, test, local_static_candidate): `harness/tests/unit/l2/test_orchestrator_round_events_cache.py:113-141 (sha256:41f334f6b73d4e43acdbbff13b2140ab609b9ddebacda957db18475fc3de6a05)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.test.2.1e95439046c43944` (static_coordinate, test, local_static_candidate): `withheld:coordinate-b21f0a63f6fe73968f46 (sha256:1e95439046c43944dc1a43cdd584209f5aed7783c6f87d2189b82a3e707185ae; source withheld from Open Alpha)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.failure.1.afee38f6ae4dad51` (static_coordinate, failure, local_static_candidate): `harness/src/towow/l2/orchestrator.py:9453-9543 (sha256:afee38f6ae4dad514d2816c66d614401a9d3a6fa6d4ce4abdbf32150483d24a2)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.1.2cdda8966cc74a48` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l2/orchestrator.py:9546-9624 (sha256:2cdda8966cc74a4886a75081a2de87bbc73940b3dc012d44657f8a3f2f7030b7)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
- `static.recovery.2.2626f44164c0fe20` (static_coordinate, recovery, local_static_candidate): `harness/src/towow/l2/execution_dispatch.py:330-358 (sha256:2626f44164c0fe20a5895b0025493443e09892569d26a122909deeb62b0b45eb)`; This is an unsealed source coordinate, not a runtime object, event, store, authority decision, or reachability observation.
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
- `M-ORCH-READYSET-EVENT-FANOUT.static-failure-1` (located_static_failure_boundary): detect `static.failure.1.afee38f6ae4dad51`; owner `unknown.failure-owner` (unknown); terminal `unknown.terminal-policy` (unknown); recovery/handoff `static.recovery.1.2cdda8966cc74a48` (candidate_recovery_boundary, local_static_candidate); negative-test locator `static.test.1.41f334f6b73d4e43`. Boundary: Static location only; trigger, retry budget, idempotence, and runtime outcome remain Unknown.

## Hash-checked static coordinates
### definition
- `harness/src/towow/l2/orchestrator.py:1049-1103 (sha256:9c65564aba1a85017f4f7484764970faf1b606f75adda6f8db0ed48374ed3eeb)`
- `harness/src/towow/l2/orchestrator.py:3977-4003 (sha256:93f3bba0490cf994810d5616eb8cb43fb7a7f55b0d532cbfc6ef1075e6980d75)`
- `harness/src/towow/l2/orchestrator.py:4006-4052 (sha256:2e6664077288999ec928f322bd0196b7eae2b5c041822c5a0039d03559dc1d8e)`
### caller
- `harness/src/towow/l2/orchestrator.py:930-936 (sha256:e6dded9d686c873fa44504def22bc5aa9437630839c3ecd2c387194a5c02d28d)`
### consumer
- `harness/src/towow/l2/orchestrator.py:10969-10981 (sha256:797f81fee6afc7ea55e6d486ad9780285d2f506180c90c6bb6cee12da3858bc1)`
- `harness/src/towow/l2/orchestrator.py:11359-11372 (sha256:8dd8397d93fd6c031bf6217c55f35b8cc645fdcb408df16f18162d8d9d8c9144)`
### test
- `harness/tests/unit/l2/test_orchestrator_round_events_cache.py:113-141 (sha256:41f334f6b73d4e43acdbbff13b2140ab609b9ddebacda957db18475fc3de6a05)`
- `withheld:coordinate-20b34cd974d482d8253b (sha256:1e95439046c43944dc1a43cdd584209f5aed7783c6f87d2189b82a3e707185ae; source withheld from Open Alpha)`
### failure
- `harness/src/towow/l2/orchestrator.py:9453-9543 (sha256:afee38f6ae4dad514d2816c66d614401a9d3a6fa6d4ce4abdbf32150483d24a2)`
### recovery
- `harness/src/towow/l2/orchestrator.py:9546-9624 (sha256:2cdda8966cc74a4886a75081a2de87bbc73940b3dc012d44657f8a3f2f7030b7)`
- `harness/src/towow/l2/execution_dispatch.py:330-358 (sha256:2626f44164c0fe20a5895b0025493443e09892569d26a122909deeb62b0b45eb)`

## Drift and unresolved questions
### Applicable drift IDs
- `DRIFT-PRIVATE-RUNTIME-001`
- `DRIFT-RUNTIME-MOCK-001`
### Unresolved
- Which daemon drives this path in production?
- No sealed source snapshot binds this local implementation to a rights-cleared public export.
- No server trace establishes which daemon, if any, drives this route in production.
- The source locates a committed-event snapshot, an in-memory candidate pool and local dispatch stamps, but which deployed state is authoritative remains Unknown.
- The ready-decision producer, polling-loop collection and batch-dispatch call are separately located; no source-resolved value-flow or sealed trace proves that a particular decision reaches a particular spawn.
- The failed-spawn branch statically calls the failure-event and main-inbound notification emitters and omits the success stamp, but runtime failure detection, notification delivery and later backlog re-entry remain Unknown.
- Static paths do not prove every plan/task type, dynamic dispatch target, authority check, reflow route, or terminal failure path is reachable.
- This superseded local anchor does not prove a production daemon drives every ready-set fan-out path.

## Claim boundary and Content Graph metadata
- No public capability claim. This card may only say that an unsealed local tree has hash-checked static coordinates and local-Git evolution anchors; it cannot claim runtime operation, production reliability, export rights, public availability, or semantic equivalence.
- Mechanism node: `mechanism.seed.M-ORCH-READYSET-EVENT-FANOUT`
- Candidate asset node: `asset.mechanism-card.m-orch-readyset-event-fanout`
- Required edge: `derived_from`; binding state: `evidence_bound_local_candidate`.
