<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flowness Drift Atlas seed — v0

Status: **experimental Open Alpha drift record**. This seed preserves findings
from both the earlier private-staging phase and the current public candidate.
Historical coordinates describe what was true when the finding was recorded;
they do not override the current release record. Findings remain blockers,
downgraders, or explicitly bounded history rather than defects hidden by a
more polished story.

| ID | Severity | Drift surface | Evidence and affected consumer | Required disposition |
| --- | --- | --- | --- | --- |
| DRIFT-OSS-CLI-001 | high | docs -> behavior | Historical private-staging docs described init/inventory/snapshot while the planning freeze rejected every CLI command. The current Open Alpha instead exposes a bounded deterministic demo and read-only inspector; the older commands remain outside the public quickstart. | Keep the historical mismatch as regression context and verify only the documented Open Alpha commands at an exact sealed successor. |
| DRIFT-OSS-CONTROL-002 | critical | policy -> controller | The historical private control path added start/stop/terminal receipts and bounded stranded-permit recovery, but a failed attempt-ledger write could still leave an in-flight permit and no sealed real-runtime recovery chain was established. The Open Alpha deterministic demo does not claim to close that private-runtime gap. | Keep the private live-execution claim excluded; require sealed runtime evidence and durable ledger-failure handling before promoting this path beyond experimental. |
| DRIFT-PRIVATE-RUNTIME-001 | high | code/test -> runtime | Orchestrator defaults to `mock_spawn=True`; real spawn requires explicit `--real-spawn`. | Do not describe local orchestration tests as real agent operation. |
| DRIFT-PRIVATE-RUNTIME-002 | high | local state -> server | Local `.towow` spawn/paused/watermark state is a cold historical backup, not a current server ledger. | Obtain a redacted server Evidence Seal; otherwise retain `runtime: unsealed`. |
| DRIFT-SERVER-DISCOVERY-001 | critical | declared deployment surface -> runtime claim | Historical private-staging reconnaissance found only bounded deployment metadata and a negative field-availability result; it did not establish a producer→event→projection→consumer→failure/recovery chain. Host labels, internal paths, source coordinates, runtime records, Transcripts, and credentials are intentionally excluded from the Open Alpha. | Treat the historical observation only as evidence that deployment claims remained Unknown. Require a separately authorized, redacted Evidence Seal before any future deployment, liveness, or private-runtime claim. |
| DRIFT-EVENT-001 | high | schema -> consumers | Legacy `NodeTouched` logical kind is wrapped in payload, so top-level consumers can lose semantics. | Inventory producers/consumers and publish a compatibility rule or adapter. |
| DRIFT-TEST-RUNTIME-003 | high | recovery test -> reality | Recovery tests use fake spawn, sleep and argv; they do not show real Codex recovery. | Add a bounded real-runtime evidence case before a recovery claim. |
| DRIFT-PUBLIC-EXPORT-004 | critical | private -> public | Historical finding: the first staging candidate had an export primitive but no executed, reviewed public export. A predecessor result was reported externally, but this mutable successor does not self-contain that evidence and cannot inherit its identity. | Bind the exact successor commit, scope, rights policy, sensitive scan, clean-room receipt, and jury evidence before publication; do not reuse predecessor identity for changed bytes. |
| DRIFT-DOC-CODE-005 | medium | narrative -> behavior | Audit documentation conflates direct signal-only and submit/blocking-fork modes. | Split public descriptions by mode and evidence. |
| DRIFT-EVENT-COUNT-001 | high | documentation -> source enum | `EventType` documentation says 71 while current source contains 124 event types. | Never use either count publicly until the sealed event inventory is generated. |
| DRIFT-STUB-BOUNDARY-001 | high | event producer -> consumer | `NodeTouched` can carry a logical kind and `stub_original_payload`; a top-level type query can lose semantic events. | Generate a producer/reducer/consumer compatibility matrix and ship an adapter or explicit rule. |
| DRIFT-RUNTIME-MOCK-001 | high | orchestration code -> runtime claim | Polling and dispatch default to `mock_spawn=True`; real spawn is opt-in. | Keep multi-agent operation unverified until a sealed real-run trace exists. |
| UNKNOWN-PHYSICAL-GATE-WIRING-001 | high | gate implementation -> enforcement | A physical gate is implemented, but local evidence does not show it is a live, steady-state enforcement point. | Record server wiring/trigger evidence or downgrade to a candidate safeguard. |
| DRIFT-DAEMON-STATUS-001 | high | daemon enumeration -> service state | Several daemons remain marked stub or owner-deferred by validation code. | Require unit/process/timer evidence per daemon before it appears in deployment material. |
| DRIFT-DORMANT-AUTOREPAIR-001 | medium | recovery code -> autonomy narrative | Sentinel repair and revive have default-off/dormant paths. | Show dormant state explicitly; do not imply autonomous repair. |
| DRIFT-PROJECTION-CONTRACT-001 | high | event schema -> projection | Registered projections do not yet prove every event/state has a reducer and consumer chain. | Build `event type → producer → reducer → consumer → test` coverage before calling the evidence plane complete. |
| DRIFT-CONFIG-PROVENANCE-001 | medium | config -> deployed behavior | Maintenance configuration is an ordinary file whose changes are not inherently evented. | Bind deployed config to a runtime seal before any configuration or safety claim. |
| DRIFT-PUBLIC-CORE-INSTALL-001 | medium | candidate package -> release-quality clean-room evidence | Historical local wheel and blank-venv results were insufficient on their own. An external predecessor clean-room result was reported for Linux aarch64 with CPython 3.12, but it is not self-contained here and cannot prove this successor, a broader platform matrix, or production readiness. | Claim no independently reproduced coordinate until the exact successor seal and non-author clean room are retained before release. |
| DRIFT-PUBLIC-CORE-CONTRACT-002 | high | Alpha cut specification -> fresh-room candidate | `flowness-ledger-core` is now a runnable experimental Open Alpha slice for proposal visibility, terminal decisions, tail recovery, projection freshness/rebuild, and read-only verdict inspection. Fresh independent clean-room and jury evidence for the exact successor remains pending and cannot be inferred from predecessor reports. | Keep the public claim at experimental Open Alpha and bind any changed successor bytes to fresh package and clean-room evidence. |
| DRIFT-RETEST-LINEAGE-001 | critical | Blocker Case -> successor release | Blocker Case v2, Rework Manifest v2, `successor-retest-attestation/v1`, and `jury-bundle/v1` implement an experimental content-bound successor route. The deterministic Open Alpha demo exercises FAIL→targeted rework→fresh retest; it does not prove model-judge quality, a general production closure system, or every private-runtime path. | Permit only the bounded experimental claim. An actual release blocker still requires its exact successor evidence, independent jury record, and owner-controlled release decision. |

## Propagation rule

Every public claim takes the lower of its mechanism status and any applicable
drift downgrade. A changed mechanism, schema or export boundary must identify
every dependent README section, diagram, demonstration, benchmark and channel
draft through the experimental Content Graph. A critical finding or a key `UNKNOWN`
blocks release rather than being averaged away by positive jury scores.
