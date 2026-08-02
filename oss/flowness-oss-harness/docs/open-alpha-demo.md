# Flowness Open Alpha: 10-minute Harness demo

This is the smallest runnable proof that Flowness is a **multi-agent harness**, not
only a ledger library or an architecture document.

It runs this lifecycle end to end:

```text
goal
  └─ 3 producers in parallel (isolated, schema-bound outputs)
       └─ sealed candidate A
            └─ 2 independent judges on the same candidate + policy
                 ├─ truth judge: FAIL (credible unsupported claim)
                 └─ structure judge: PASS
                      └─ whole candidate BLOCKED — no score averaging
                           └─ targeted rework of one claim
                                └─ successor candidate B + new evidence
                                     └─ 2 fresh independent judges: PASS
                                          └─ accepted trace
```

## Run it without a model account

From `oss/flowness-oss-harness` in a Git checkout, or from a scratch copy of
that package made outside a sealed-export directory:

```bash
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/flowness-oss open-alpha-demo --output /tmp/flowness-open-alpha-demo
.venv/bin/flowness-oss open-alpha-demo-inspect --run-root /tmp/flowness-open-alpha-demo
```

The first command creates the run. The second command is read-only and verifies
the entire chain again from bytes on disk. Both commands finish with a JSON
summary containing:

```json
{
  "state": "verified",
  "producer_agents": 3,
  "judge_agents_per_round": 2,
  "round_1": "blocked",
  "blocker_id": "BLK-DEMO-TRUTH-001",
  "targeted_rework": "verified",
  "round_2": "accepted"
}
```

Use a new output directory for each run. Existing output is never overwritten.

### Public resource boundary

The demo's public runtime bundle requires exactly its three JSON Schemas:

- `schemas/open-alpha-demo-producer-result.schema.json`
- `schemas/open-alpha-demo-jury-report.schema.json`
- `schemas/open-alpha-demo-trace.schema.json`

Resource-root discovery uses those files only as bundle-identity sentinels. It
does not require private release-preparation configuration such as
`config/execution-policy.json` or `config/source-boundaries.json` before the
demo can start. Commands that consume a policy, role registry, or another
schema still load and validate their own dependencies and fail closed with a
consumer-specific error when one is absent. Finding the public bundle is not
evidence that every optional or private command is configured.

In an installed environment, package data lives under
`<sys.prefix>/flowness-oss-harness/{config,schemas}`; a source checkout keeps
the same directories at its project root.

## What is real in the deterministic runner

- Three producer workers execute concurrently behind a three-party barrier.
- Each producer writes to its own `agents/<role>/` directory.
- Every producer result is validated against a JSON Schema before candidate
  assembly.
- Both first-round judges bind the exact same candidate bytes and policy bytes.
- One credible `FAIL` blocks candidate A even though the other judge passes.
- Rework changes only the failed claim's maturity and limitation fields.
- Candidate B has a different content-bound identity and points to new successor
  evidence.
- Two fresh judge identities re-evaluate candidate B and both pass.
- `events.jsonl` is sequence- and hash-linked. `trace.json` binds every candidate,
  report, rework artifact, evidence artifact and event-log byte hash.
- The inspector rejects tampering, candidate/policy substitution, missing agent
  isolation, average-based override and unrelated rework.

## What it does not prove

The default `fixture` runner validates orchestration semantics. It does **not**
measure model reasoning quality, production reliability, hosted fleet behavior,
external adoption or performance at scale. Those remain separate evidence gates.

The deliberately bad first claim is not marketing copy. It is a negative fixture:
the truth judge must reject a `current_verified` claim that has only one fixture
evidence reference and no runtime, test or event evidence.

## Optional Codex CLI producer mode

If Codex CLI is installed and authenticated, the same harness can replace the
three producer fixtures with three real, parallel Codex processes:

```bash
.venv/bin/flowness-oss open-alpha-demo \
  --runner codex \
  --codex-bin codex \
  --output /tmp/flowness-open-alpha-codex-demo
```

Each Codex process receives a different mission, a read-only sandbox, a distinct
working directory and the same producer response schema. The judges remain
deterministic policy probes. This preserves the required FAIL → rework → PASS
teaching path and prevents model variance from silently weakening the gate.

## Inspect the chain

The useful entry points in a completed run are:

```text
trace.json                         complete content-addressed index
events.jsonl                      append-order, hash-linked lifecycle
policy.json                       exact no-average policy used by both juries
agents/*/result.json              three isolated producer results
candidates/round-1.json           blocked candidate A
jury/round-1/*/report.json        independent FAIL and PASS
rework/manifest.json              exact blocker and allowed ripple
rework/successor-evidence.json    new evidence for the successor
candidates/round-2.json           successor candidate B
jury/round-2/*/report.json        two fresh PASS reports
```

The canonical test below is **Git-checkout only**. It intentionally calls
`git ls-files` while checking the selected public Harness surface, so it is not
the verifier for a bare sealed export:

```bash
.venv/bin/pytest tests/test_public_harness_package.py \
  -k installed_oss_wheel_exposes_only_working_public_commands
```

This command is run from `oss/flowness-oss-harness` in the Git checkout used
above. The narrower private development test is intentionally not part of the
Open Alpha payload.

For a bare sealed export, keep the original export directory immutable and run
the repository-independent verifier from an already installed staging
environment:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m flowness_oss_harness.rc0_sealed_export verify \
  --export-root /path/to/flowness-open-alpha-rc0
```

Run the separate `cleanroom_acceptance` entry against that same export to prove
fresh offline installation and the selected E2E stages. If you want to run the
demo from an export, copy the package to a scratch directory first; never create
`.venv`, test output, or bytecode inside the sealed directory.
