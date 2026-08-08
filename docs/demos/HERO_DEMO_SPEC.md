# Hero Demo Specification: Work Outlives Agents

**Status:** `[DESIGNED]`  
**Target duration:** 60–90 seconds for the visual demo; under 5 minutes for the reproducible CLI path.  
**Purpose:** make the Flow Engineering category visible before explaining the internal mechanism inventory.

## 1. The single claim

> **A Work object survives agent loss, compiles a new execution from current state, changes its graph after a real Finding, and cannot close until reality evidence and the required human authority exist.**

The demo must not attempt to prove every Flowness mechanism. It should prove one memorable property with a replayable trace:

```text
agent A dies
work W-42 remains alive
agent B is assembled from current state
new evidence invalidates part of the graph
graph v1 becomes graph v2
code passes but activation remains unknown
one prepared owner decision unblocks the irreversible boundary
work closes with fresh, release-bound evidence
```

## 2. Narrative fixture

Use a compact software target that ordinary developers understand:

> Add a safe pause command to a small scheduler and wire it into the real command path.

The fixture repository should contain:

- a scheduler core;
- a CLI or HTTP consumer;
- tests;
- a production-like entry path;
- an intentionally missing consumer wire;
- an operator-acceptance condition;
- deterministic fake agents by default;
- optional real-model executors behind the same provider interface.

The fixture must be licensed and committed in the public repository.

## 3. Scene-by-scene behavior

### Scene 1 — Work exists before an Agent

Command:

```bash
flowness-oss work-demo start --output /tmp/flowness-flow-demo
```

Display:

```text
WORK W-42
state: ready
agents: none
flow: alive

goal:
  add a safe pause command and wire it into the real scheduler path
```

Acceptance:

- `WorkCreated` is committed before any execution/session event;
- `flowness-oss work show W-42` works with zero active agents;
- Work identity is stable across process restart.

### Scene 2 — Current execution is compiled

Display the assembly, not an opaque agent launch:

```text
compiled at seq: 120
capsule: sha256:...
graph: G-W42-v1
capability: scheduler-code-change
obligations:
  - no-unreviewed-production-path-change
validators:
  - unit-tests
  - consumer-coverage
```

Acceptance:

- every capsule source has one committed cutoff;
- graph version and capsule hash are persisted;
- the execution records exact source commit and fixture version.

### Scene 3 — Agent A is killed

The demo intentionally terminates the first executor after a partial change.

Display:

```text
EXECUTION E-1
state: lost
reason: simulated-worker-crash

WORK W-42
state: active
agents: none
flow: alive
recovery: reconcile execution gap
```

Acceptance:

- no manual state edit is required;
- a pending expectation times out or observes worker loss;
- the Work remains live and queryable;
- the first execution’s partial artifact is either isolated or explicitly recovered.

### Scene 4 — Agent B is assembled

Reconciliation compiles a fresh execution from current committed state.

Display:

```text
EXECUTION E-2
executor: deterministic-provider-B
source: current Work state at seq 147
not a replay of E-1 transcript
```

Acceptance:

- E-2 does not inherit hidden process memory;
- only committed artifacts and a fresh capsule are visible;
- provider identity may differ without changing Work identity.

### Scene 5 — A real Finding changes the graph

The implementation passes unit tests but the consumer-coverage validator finds that no organic entry path calls the pause command.

Display:

```text
FINDING F-19
kind: commitment-gap
fact:
  pause implementation exists
  production command path has no consumer edge
recommended_reflow: repair

GRAPH
  G-W42-v1 → G-W42-v2
  + task: wire production consumer
  + validator: organic invocation readback
```

Acceptance:

- the Finding binds exact source and graph versions;
- the old local completion claim becomes blocked/suspect;
- only the affected slice reopens;
- graph v2 is derived from the new Work state, not a hard-coded visual branch.

### Scene 6 — Built is not activated

After repair, tests and consumer wiring pass. The system still refuses final closure because organic activation evidence is absent.

Display:

```text
effect state
  built: yes
  integrated: yes
  activated: unknown
  accepted: no
```

Acceptance:

- test, demo, drill, or synthetic invocation cannot satisfy organic activation;
- the system either waits for an organic event or requests an explicit owner waiver;
- uncertainty is preserved.

### Scene 7 — Human returns at the load-bearing boundary

Owner Inbox item:

```text
BLOCKING DECISION OD-7

Choose the closure condition:
A. wait for one organic invocation in the fixture's production path
B. issue an explicit demo-only waiver, which forbids a production-ready claim

Recommendation: A
Why: the launch claim includes real-path activation
```

Acceptance:

- the decision includes authoritative context, options, recommendation, and consequence;
- it does not require reading execution transcripts;
- the human’s answer is committed as an authority-bearing event;
- the system cannot forge the owner principal.

### Scene 8 — Fresh closure

After an organic invocation or the appropriately bounded waiver:

```text
WORK W-42
state: closed
agents: none
flow: closed

effect:
  built=yes
  integrated=yes
  activated=verified
  accepted=yes

release binding:
  source_commit: ...
  fixture_sha256: ...
  evidence_manifest_sha256: ...
```

Acceptance:

- the final validator reads the successor artifact and fresh evidence;
- the release manifest binds source commit, demo output, schemas, and evidence hashes;
- `work-demo-inspect` reconstructs the claim without trusting the rendered UI.

## 4. Required commands

Recommended public commands:

```bash
flowness-oss work-demo start --output PATH
flowness-oss work-demo continue --run-root PATH
flowness-oss work-demo choose --run-root PATH --decision OD-7 --option A
flowness-oss work-demo inspect --run-root PATH

flowness-oss work show W-42 --run-root PATH
flowness-oss work explain W-42 --why-blocked --run-root PATH
flowness-oss work graph W-42 --run-root PATH
flowness-oss work evidence W-42 --run-root PATH
```

The default `start` command may run the entire deterministic sequence, but the individual stages must remain inspectable and replayable.

## 5. Artifact layout

```text
run-root/
├── release-binding.json
├── event-log.jsonl
├── projections/
│   ├── work-view.json
│   ├── graph-v1.json
│   └── graph-v2.json
├── capsules/
│   ├── e1.json
│   └── e2.json
├── executions/
│   ├── e1/
│   └── e2/
├── findings/f19.json
├── decisions/od7.json
├── evidence/
│   ├── tests.json
│   ├── consumer-coverage.json
│   ├── organic-activation.json
│   └── acceptance.json
└── manifest.sha256
```

## 6. Determinism and optional model mode

The public qualification mode must be deterministic and model-free. It tests runtime semantics, not model capability.

An optional provider mode may use Codex, Claude Code, or another coding agent. It must produce the same frozen event/evidence interface and must not receive evaluator-private expected labels.

The provider mode should be presented as a demonstration, not as qualification evidence until the experiment controls are frozen.

## 7. Failure-injection switches

The demo should support controlled mutation:

```bash
--disable-reconcile
--disable-consumer-coverage
--allow-synthetic-activation
--reuse-old-verdict
--drop-finding-route
--bind-certificate-to-floating-path
```

Each switch should reproduce a specific Failure Atlas case. This turns the demo into a small falsifiable laboratory rather than a theatrical happy path.

## 8. Visual requirements

The UI must keep Work visually stable while Agents and Graph versions change.

Always display:

- Work ID and current state;
- fact watermark;
- active Agent count;
- `flow: alive|closed|dead-lettered`;
- current graph version;
- blocker or next action;
- effect / activation / acceptance states;
- evidence status.

The decisive frame is:

```text
agents: none
flow: alive
```

## 9. Release gate

The README may switch from the current-safe version to `docs/internal/launch-staging/README.after-flow-demo.md` only when:

- all acceptance criteria above pass in CI;
- a clean clone reproduces the run offline or with only declared dependencies;
- the source commit and release artifact hashes are frozen;
- the rendered video is derived from the same bound run;
- the claim register changes the demo from `[DESIGNED]` to `[RUNNABLE]`;
- an independent reviewer verifies that the demo is not a prewritten branch masquerading as dynamic reflow.
