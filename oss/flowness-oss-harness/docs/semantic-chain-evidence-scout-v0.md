# Semantic-chain evidence scout — v0 (private)

**Scope.** This is an independent, read-only static/test audit of the fifteen
seed mechanisms. It deliberately starts from symbol references and call sites,
not from the existing mechanism-card `caller` slot. It is a rework input, not
an Evidence Seal, public claim, readiness promotion, or replacement for a
runtime trace.

**Snapshot boundary.** Paths and line coordinates below were observed in the
local worktree. They are useful only while their cited source is still the
reviewed source. No sealed export, server event payload, transcript, daemon
configuration, model execution, or owner action was read for this report.

## Method and meaning

For every mechanism I performed an inverse symbol scan over `harness/src`,
`harness/scripts`, and `public-core/.../src`, then compared the result with the
existing static-chain candidate. A *direct static edge* means the named call is
present in executable source. A *test edge* means a test executes or asserts a
local path. Neither is runtime reachability.

The important anti-template rule is:

> A “caller” is not accepted merely because it is adjacent to a definition,
> has the right name, is a wrapper/helper, or appears in a test. We record the
> actual callee symbol, whether the call leaves the helper, and the first
> observed state/event consumer. If the only edge is a test or helper, it stays
> explicitly incomplete.

`static+test` below means a source call and a local test anchor were found.
`static-only` means the source chain was found but the cited test does not
exercise the whole semantic route. `candidate-only` means the only non-test
caller is the local OSS/demo candidate, not a Harness runtime entry.

## Matrix

| Mechanism | Observed caller -> definition -> state/event -> consumer | Evidence | Counterexample / unproved link |
| --- | --- | --- | --- |
| `MECH-EVT-001` committed-visible batch ledger | `Ledger.begin_proposal`, `append_proposed`, and `decide` call private `Ledger._append` (`public-core/flowness-ledger-core/src/flowness_ledger_core/ledger.py:179-220` -> `:151-177`). `_append` persists proposal/proposed-record/decision rows; `Ledger.read("committed")` exposes only accepted proposed records (`:222-232`). | `static+test`: `tests/test_ledger.py:12-21` checks invisible pending rows, accepted visibility, and reopen/read equivalence. A local demo calls public wrappers at `src/flowness_ledger_core/demo.py:59-69`. | `_append` is a private helper; demo/scenario callers are candidate artifacts, not a server caller. No external installer consumer, sealed export, concurrent deployment, or runtime recovery use is proved. |
| `MECH-EVT-002` cross-process sequence/provenance | `EventLog.append_transaction_batch` enters `_file_lock` (`harness/src/towow/l0/event_log/event_log.py:674-711` -> `:544-557`); `_append_direct_record` also enters it (`:904-946`), then sequence is derived, record stamped and appended. `CommitGate.attempt_commit` hands an accepted batch to `append_transaction_batch` (`harness/src/towow/l0/commit_gate/gate.py:909-923`). | `static+test`: all discovered direct lock consumers are `event_log.py:687,911,1222,1933`; `tests/unit/l0/test_event_log_concurrency.py:389-431` performs the lock-vs-no-lock counterfactual. | The definition’s adjacent `append_transaction_batch` is a valid direct caller, but it is not the full producer chain. `segments.append_raw_event_line` remains a weaker port; producer coverage, cross-host filesystem semantics, real writer identity and recovery run are unproved. |
| `MECH-COMMIT-001` envelope-to-gate | `_submit_with_audit` calls `gate.attempt_commit(..., audit_blocking=True)` (`harness/src/towow/cli/main.py:1689-1714`). Other non-test callers include `l0/obligations/lifecycle.py:159,253`, `cli/closure_commands.py:154,274`, `l2/daemon_run_once.py:732,816,1275,1484`, and many CLI paths. `attempt_commit` constructs the accepted batch and calls EventLog append (`l0/commit_gate/gate.py:880-923`); views are refreshed after acceptance. | `static+test` for gate primitive: `tests/unit/test_scan_abandoned_perf.py:120-142` is an abandoned-envelope recovery anchor. The source scan disproves a single-CLI-caller story. | Many callers do not establish that all writes enter the gate; direct Path-B producers exist. The cited test is not an end-to-end real audit fork/commit/recovery trace. Active check set, deployed audit mode, and real acceptance are unknown. |
| `MECH-PROJ-001` replayable projection/watermark | `rebuild_type_projection` reads committed ledger rows and persists a watermark (`public-core/flowness-ledger-core/src/flowness_ledger_core/projection.py:30-66`); `read_fresh_type_projection` validates hash and rejects a stale watermark (`:69-83`). The prior “caller” was test-only, but the inverse scan finds local `scenario_pack.py:118,134` as the only non-test caller. | `candidate-only+test`: `tests/test_projection.py:8-34` proves accepted/rejected/pending handling and stale refusal/rebuild in a temp ledger. | `scenario_pack` is a generated local explanatory scenario, not a runtime projection worker. There is no canonical runtime caller, worker schedule, projection consumer, or server stale/rebuild record. |
| `M-ORCH-READYSET-EVENT-FANOUT` | `OrchestratorDaemon` dispatch calls `self._ready_execution_decisions(...)` within the hash-bound caller excerpt `harness/src/towow/l2/orchestrator.py:930-936` (`sha256:e6dded9d686c873fa44504def22bc5aa9437630839c3ecd2c387194a5c02d28d`); the definition is `harness/src/towow/l2/orchestrator.py:1049-1103` (`sha256:9c65564aba1a85017f4f7484764970faf1b606f75adda6f8db0ed48374ed3eeb`). The ready-decision collector is `harness/src/towow/l2/orchestrator.py:10969-10981` (`sha256:797f81fee6afc7ea55e6d486ad9780285d2f506180c90c6bb6cee12da3858bc1`) and polling batch consumer is `harness/src/towow/l2/orchestrator.py:11359-11372` (`sha256:8dd8397d93fd6c031bf6217c55f35b8cc645fdcb408df16f18162d8d9d8c9144`). Failure/no-stamp handling is `harness/src/towow/l2/orchestrator.py:9453-9543` (`sha256:afee38f6ae4dad514d2816c66d614401a9d3a6fa6d4ce4abdbf32150483d24a2`); recovery/backlog handling, including backlog recomputation, is `harness/src/towow/l2/orchestrator.py:9546-9624` (`sha256:2cdda8966cc74a4886a75081a2de87bbc73940b3dc012d44657f8a3f2f7030b7`), and the downstream dispatcher is `harness/src/towow/l2/execution_dispatch.py:330-358` (`sha256:2626f44164c0fe20a5895b0025493443e09892569d26a122909deeb62b0b45eb`). | `static+test`: `harness/tests/unit/l2/test_orchestrator_round_events_cache.py:113-141` (`sha256:41f334f6b73d4e43acdbbff13b2140ab609b9ddebacda957db18475fc3de6a05`) anchors cached round-event behavior. | A source-level method and recovery chain is real, but it does not prove the polling loop is enabled or every task type reaches the branch. No live event -> ready-set -> spawned-session trace, deployed authority, or runtime causal chain is proved. |
| `M-ORCH-SINGLE-EXEC-CLAIM-FENCING` | Spawn path calls `claim_exec_spawn` before spawning (`harness/src/towow/l2/orchestrator.py:9395-9446` -> `:1516-1527`), rechecks the dispatch stamp, renews, passes fencing token into spawn and releases in `finally`. Fencing validation is consumed by `l0/commit_gate/fencing_check.py:74-141`. | `static+test`: `tests/integration/test_exec_claim_reaper.py:67-100` anchors claim/reaper behavior. The source route is substantially stronger than a bare helper reference. | This proves one observed orchestrator spawn route, not every execution dispatch route. Enforce-off configuration, shared-filesystem assumptions, multi-host behavior, and a real late-writer rejection are unknown. |
| `M-ORCH-WATERMARK-BACKLOG-DECOUPLING` | Main polling loop unconditionally calls `save_watermark_atomic` after preserving non-exec backlog markers and ready-set rescan logic (`harness/src/towow/l2/orchestrator.py:11444-11453` -> `:1356-1361`). An ignition/skip route also calls it (`:7040-7054`); runtime loop constructs daemon from `load_watermark` (`:10877-10880`). | `static+test`: `tests/unit/test_fb3_watermark_decouple.py:215-256` is the targeted decoupling test. | There are two source callers, so the prior one-caller row was incomplete; neither proves marker durability under load, every truncation/retry branch, live backpressure, or deployed capacity. |
| `M-REFLOW-OUT-OF-BAND-RECONCILIATION` | `reconcile_stranded_reflow` calls `scan_stranded_worktrees` then `emit_stranded_reflow_findings` (`harness/src/towow/l2/reflow_reconcile.py:466-471`; definition `:233-300`, emitter `:388-463`). `run_sentinel_once` independently scans SLA-stranded work, writes inventory, emits findings, and closes disposed findings (`reflow_sentinel.py:398-435`). Its module CLI declares an out-of-band process entry (`:438-445`). | `static+test`: `tests/integration/test_reflow_sentinel.py:160-179` proves stale-marker backstop re-emission in local fixtures. | The actual call path is broader than the preselected sentinel slot, but no cron/launchd timer, inventory subprocess, promote result, or successful closure event was read. It proves detection/emission branches, not universal recovery. |
| `M-SESSION-LIVENESS-AND-DEADLETTER` | Orchestrator calls `assess_session_liveness` (`harness/src/towow/l2/orchestrator.py:6251-6303` -> `session_liveness.py:194-257`); other source callers occur at `orchestrator.py:6664,8918` and `reconcile_loop.py:912`. A periodic loop calls `dead_letter_inbox.sweep_aged_out` (`orchestrator.py:10768-10789` -> `dead_letter_inbox.py:629-652`); `enqueue` state machine begins at `:352-393`, and `redispatch_failed` returns to enqueued or terminalizes at cap (`:531-567`). | `static+test`: `tests/unit/test_session_liveness.py:182-206`; `tests/unit/l2/test_dead_letter_inbox.py:212-251`; wiring test `tests/unit/l2/test_gap6_dead_letter_drain_wiring.py:91-140`. | Multiple paths refute a single caller template, but they do not prove every liveness verdict routes to dead letter. Roster/PID actuality, deployed TTL sweep and any automatic revival are unknown. |
| `MECH-REVIEW-001` event-folded review verdict | `compute_review_verdict_over_set` builds fold events and calls it in gate checking (`harness/src/towow/l0/commit_gate/review_verdict_check.py:199-219`); it reaches `fold_review_verdict` via `review_verdict.py:204-225` -> `:99-130`. Gate rejection consumes a non-passed verdict. | `static+test`: `tests/unit/l0/test_lnd03_review_verdict_fold.py:78-93` checks lifecycle fold behavior. | The original `review_verdict_check.py:142-248` is not a direct caller of the pure fold symbol—it calls the higher `compute...` function. This report records the intermediate state transformation rather than pretending it is a direct call. No cross-session production event chain proves all review completions enter gate. |
| `MECH-CLOSURE-001` contract-side closure recomputation | `execution_done_recompute` calls `verify_closure_against_contract` (`harness/src/towow/l1/execution_done_recompute.py:184-230`, direct call `:199-207`); CLI invokes it during finding closure (`harness/src/towow/cli/main.py:9672-9690`). Result becomes an execution blocking check / derived closure state. Additional non-test callers appear in `schemas/payloads/fix_completed_checks.py:190` and CLI `:9229`. | `static+test`: `tests/unit/l1/test_closure_verification.py:177-188` covers local contract verification. | The `execution_done_recompute` file is a direct source caller, but local subprocess checks are not evidence a real worktree/ledger was authoritatively closed. Known integration mismatch and authoritative deployed closure command remain unresolved. |
| `MECH-CAPSULE-001` provenance-bound capsule | Production submit/audit driver calls `assemble_audit_capsule`, which calls `assemble_capsule` (`harness/src/towow/cli/main.py:1722-1729`; `harness/src/towow/l1/audit_fork.py:155-183` -> `l0/capsule/pipeline.py:229-693`). The pipeline emits/returns capsule state at `pipeline.py:610-694`; failure path `_abort_capsule_assembly` is `:1900-1949`. Additional CLI call sites are `main.py:5031,5133`. | `static+test`: `tests/integration/test_t_fix_a_capsule_pipeline.py:49-95` executes all stages in an isolated log. | `audit_fork.py:158-187` is a real source call, not a test, but no sealed downstream prompt-consumer attribution or deployed capsule is present. Test proves event-log fixture flow, not context delivery. |
| `MECH-PROV-001` forward provenance anchor | `emit_provenance_anchor` calls `build_provenance_anchor_intent` (`harness/src/towow/l0/event_log/provenance_anchor.py:101-139` -> `:41-98`) and then writes Path-B. Inverse scan found **no non-test source caller** of `emit_provenance_anchor`; it is not valid to call its own helper a production chain. | `test-only`: `tests/integration/test_prov_anchor.py:146-220` writes and reads a temporary EventLog and asserts subject indexing. | This is a deliberate negative result: helper -> emitter exists, but emitter -> live producer/consumer does not. No continuously-running anchor producer, consumer or runtime link is proved. |
| `MECH-VERIFY-001` independent verification fork | CLI closure code directly calls `dispatch_fork(..., family=GATE)` (`harness/src/towow/cli/main.py:9692-9737` -> `harness/src/towow/l1/verification_fork.py:1661-1759`); audit fork also calls the same entry (`l1/audit_fork.py:219-230`). Verdict parsing/schema validation is downstream (`verification_fork.py:1520-1545`) and timeout/resume recovery is `:1468-1518`. | `static+test`: `tests/integration/test_run059_authortime_driver.py:327-365` exercises failure overriding a claimed closure. | There are many direct non-test call sites (`cli/main.py:5498,5604,8527,9453,9713,10209,14118,15112,15633`; `l2/verify_observer_scan.py:257`), but tests may use replay. No real independent model/context/tool boundary, deployed sandbox or live lifecycle corpus is proven. |
| `MECH-OWNER-001` owner-answer reflow | Orchestrator resolves target then calls `deliver_goal_answer` (`harness/src/towow/l2/orchestrator.py:8372-8419` -> `l2/escalation_reflow.py:279-310`). `UNRESOLVED` follows bounded retry/dead-letter (`orchestrator.py:8420-8449`); exceptions take the same terminalizing path (`:8451-8473`). The periodic loop invokes `drive_escalation_answer_reflow` (`:10785-10789`). Separate CLI source caller exists at `cli/main.py:19992`. | `static+test`: `tests/integration/test_r08_hard_closure_wiring.py:144-189` covers the conservative unresolved-session case. | The static chain intentionally proves no false applied result for an unresolvable session. It cannot prove a real owner answer was injected, appeared in a target transcript, resumed useful work, or that UI “resolved” equals delivery. |

## Findings for the rework agent

1. **Do not retain a single `caller` field.** At minimum distinguish
   `direct_source_callers`, `entrypoint_callers`, `test_callers`, and
   `helper_only_callers`. `MECH-PROV-001` must remain helper/test-only;
   `MECH-PROJ-001` must remain candidate-only; `MECH-EVT-001` is public-wrapper
   to private primitive rather than a deployed entry.
2. **Make the intermediate semantic edge explicit.** For example,
   `MECH-REVIEW-001` is `check_review... -> compute_review_verdict_over_set ->
   fold_review_verdict -> non-passed gate rejection`, not a direct caller claim
   from the checker to the pure fold. `MECH-EVT-002` needs the gate->batch->lock
   chain, not merely `append_transaction_batch -> _file_lock`.
3. **Keep two independent ceilings.** Static/test evidence may improve a
   mechanism card’s explanation, but cannot change `experimental`,
   `current_verified`, release readiness, or public claim status. A runtime
   Evidence Seal is still needed for producer identity, event/state readback,
   consumer reaction, and failure/recovery in the same bounded trace.
4. **Carry negative results forward.** The missing production caller for
   provenance anchor, candidate-only projection caller, Path-B/raw append
   bypass surface, enforce-off fencing mode, and owner-answer delivery gap are
   material explanatory facts, not documentation omissions to hide.

## Minimum next evidence, if/when the sealed collection is authorized

For each runtime-bound mechanism, collect one redacted, immutable run slice
with: producer identity/config digest, input trigger, canonical event/state
readback, named consumer effect, and a failure or recovery observation. For
the three incomplete categories above, first collect a canonical production
entrypoint binding; no amount of prose or unit-test expansion substitutes for
that edge.
