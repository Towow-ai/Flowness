# Mechanism excavation seed map

Status: discovery map only. It is not a Mechanism Registry, runtime evidence,
or a public capability claim. Its purpose is to prevent the first excavation
wave from mistaking README language, static names, or local cold state for the
running Flowness system.

## First trace set

Each trace must begin from current private Harness code and terminate at an
authoritative state, consumer, failure path, and recovery path. A completed
card needs at least two independent evidence groups, including code, test, or
runtime evidence. Server evidence is required before any current-runtime
claim.

| Trace | Primary code path | Required second evidence | Known anti-claim |
| --- | --- | --- | --- |
| Event → state → projection | `l0/event_log/*` → `l0/commit_gate/*` → `l0/projection/projection.py` | event schema producer/consumer plus conformance or concurrency test | Event names do not prove every writer crosses the commit gate. |
| Ready task → delegated agent → outcome | `l2/orchestrator.py` → `execution_dispatch.py` → `run_owned_agent.py` | dispatch/polling test and server watermark/session/outcome chain | `spawn_mode=REAL` does not prove a live remote dispatch. |
| Review → finding → fix → acceptance | `l1/review_finding.py` → verification/closure modules → `review_verdict.py` | closure-gate tests and a terminal/retest event chain | A review event alone is not completion. |
| Failure → sentinel/reflow → recovery | `awareness/*`, `l2/sentinel_loop.py`, `reflow_*`, `dead_letter_inbox.py`, `revive.py` | failure/recovery test and server-side dead-letter or recovery record | A systemd unit or daemon file does not prove deployment. |
| Context and owner judgment | `l0/capsule/*`, `l1/judgment_retrieval.py`, `l2/transcript_efficiency.py` | sealed/redacted Transcript excerpt plus downstream code/event coordinate | Transcript text is a candidate insight, not an implementation claim. |

## Server evidence seal requirements

The Evidence Seal wave must capture an internally consistent, redacted set of:

- canonical event segments or log identity; graph/projection cursor; orchestrator
  watermark; active claims and outcomes;
- exact deployed service/unit identity and configuration hash; process/session
  evidence only where it is relevant to a mechanism card;
- a selected set of real failure, retry, dead-letter, recovery, and owner-takeover
  records;
- the private source commit/tree identity and clean/dirty status.

The local `harness/.towow` tree is historical/cold evidence only. It can guide
selection, but cannot establish the current server state.

## Initial drift probes

Every excavator must emit explicit findings or an explicit no-result for these
surfaces:

1. event schema → actual producer/consumer, including restricted raw/path-B
   compatibility routes;
2. code → tests → runtime, especially daemon, kill-switch, paused/unfreeze and
   recovery behavior;
3. projection file/reducer → cursor/watermark → canonical event history;
4. systemd/unit source → installed service → observed execution;
5. private Harness mechanism → public OSS export and any candidate claim;
6. English canonical explanation → Chinese and channel adaptations once they
   exist.

## Outputs expected from the first real excavation wave

- Mechanism Cards with status, source coordinates, state transition, failure and
  recovery path, confidence, and unresolved questions;
- Unknown records for any executable entrypoint, durable state, event type,
  projection, daemon, terminal path, or owner gate that cannot be traced;
- Drift findings with affected consumer, severity, public impact, and a precise
  retest condition.

No prose, diagram, benchmark, or channel material may promote an item beyond
the status established by those outputs.
