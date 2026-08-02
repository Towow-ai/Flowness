<!-- SPDX-License-Identifier: CC-BY-4.0 -->

<div align="center">

# Flowness

### Turn a vague engineering goal into design, engineering decisions, parallel work, and an independently accepted outcome.

Flowness is not about making more agents write code at once. It gives complex
agent work a cognitive pipeline before execution, a governed way to return to
the right upstream layer when something is wrong, and evidence-backed
acceptance at the end.

[English](README.md) · [简体中文](README.zh-CN.md) · [Run the demo](oss/flowness-oss-harness/docs/open-alpha-demo.md) · [Architecture overview](docs/architecture.md)

</div>

![Flowness lifecycle: interview, design, engineering specification, consensus, planning, parallel execution, independent review, rework, and acceptance](docs/assets/flowness-lifecycle.svg)

> **Available today:** the public Alpha runs the execution → review → targeted
> rework → acceptance kernel. Design and engineering-spec are the next opening
> slice; their private implementation is partial and has no organic end-to-end
> proof yet.

## The problem is not agent count

A multi-agent run can look busy while still failing in predictable ways:

- the original goal was never clarified;
- product choices leaked directly into code without a design decision;
- agents implemented incompatible assumptions because no engineering contract was frozen;
- a reviewer found a symptom, but the real mistake belonged to planning or design;
- the producing agent declared itself done and the system had no stronger definition of done.

Flowness treats those as different failures at different layers. It turns an
ambiguous request into explicit artifacts, lets specialized agents work in
parallel, and makes a credible `FAIL` or critical `UNKNOWN` survive until the
underlying blocker is corrected and independently retested.

## One pipeline, not a bag of prompts

The complete Flowness lifecycle is:

`goal → interview → design → engineering spec → engineering consensus → plan → parallel execution → review → fix/reflow → accepted outcome`

| Stage | What it produces | What it prevents |
| --- | --- | --- |
| Interview | A work-ready brief, constraints, and open information needs | Solving the wrong problem confidently |
| Design | Competing options, explicit trade-offs, falsifiable predictions, and a frozen design body | Correct code for the wrong product or behavior |
| Engineering spec | Components, interfaces, state, failure semantics, parameters, and test architecture | Agents implementing mutually incompatible interpretations |
| Engineering consensus | Versioned concepts, invariants, state machines, consumers, and supersession rules | Agreements drifting silently across sessions |
| Planning | A dependency-aware task graph with design, engineering, and consensus references | Parallel activity that cannot be integrated or accepted |
| Execution | Isolated work, declared read/write scope, artifacts, and evidence | Hidden conflicts and unverifiable “done” claims |
| Review and fix | Independent findings, stable blocker identity, targeted rework, and fresh retest | Self-approval and score averaging that hide real failures |

Not every task should pay for every stage. The routing rule is to keep small,
reversible work short and promote high-impact work to the full pipeline. In the
private runtime this still has a manual fallback: automatic tier routing is not
yet a complete machine-enforced guarantee.

## Three ideas to remember

### 1. Cognitive compilation

Flowness progressively compiles human intent into things machines can execute
and reviewers can falsify: a brief, a design, an engineering specification,
frozen consensus, a task graph, artifacts, findings, and acceptance evidence.
The point is not more documentation. Each layer must change downstream
behavior.

Deep dive: [how the design and engineering rings differ](docs/design-engineering-rings.zh-CN.md)
(Chinese-first, with an English summary).

### 2. Layer-aware reflow

When work fails, “try again” is not always the right answer. Flowness models
six correction depths so a failure can return to the nearest layer that must
genuinely change:

`re_execute → repair → replan → re_engineer → redesign → re_interview`

Moving farther upstream costs more, so the routing contract requires an
exclusion trail: why a lighter correction cannot solve the problem. The schema,
CLI, and some routes exist, but route maturity varies: `replan` has real history;
design and engineering reflow are still being closed and organically proven.

### 3. Evidence-backed acceptance

“The agent said done” is not a terminal state. Candidates are content-bound,
judges are separated from producers, findings persist across rework, and a
fresh jury evaluates the successor. One credible mandatory failure blocks the
outcome; it cannot disappear into an average score.

## A 60-second real case: finding our own missing upstream layers

Flowness's reconstruction exposed a structural flaw in Flowness itself. Work
could be delegated to multiple agents, but the system could not reliably carry
forward *why a direction was chosen* or *which engineering facts every agent
must share*. The reconstruction separated those concerns: design now records
alternatives, trade-offs, scenarios, and falsifiable predictions; engineering
specification defines components, interfaces, state, failure semantics, and
test architecture before planning begins.

That is real implementation work, not a finished success story. The main
objects, CLI, gates, tests, and some forward/reflow routes exist in the private
runtime. Automatic tier routing and the engineering-spec → consensus publishing
chain are not closed, and there is no organic end-to-end run yet. The public
demo below proves the narrower acceptance kernel only.

Another dogfood run reached a more concrete failure: every gate was green, but
one owner question exposed a missing runbook, an uncalled safety path, and four
unmapped consumers. Read [how “soft rules” became executable gates](docs/cases/green-gates-empty-delivery.md).

## Run the public proof in 10 minutes

The Open Alpha ships a deterministic, inspectable proof of the execution →
review → rework → acceptance kernel. It launches three isolated producers,
blocks the first candidate on a mandatory failure, performs targeted rework,
uses two fresh deterministic policy judges, and verifies the resulting trace.

```bash
git clone https://github.com/Towow-ai/Flowness.git
cd Flowness
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ./oss/flowness-oss-harness
.venv/bin/flowness-oss open-alpha-demo \
  --output /tmp/flowness-open-alpha-demo
.venv/bin/flowness-oss open-alpha-demo-inspect \
  --run-root /tmp/flowness-open-alpha-demo
```

The package declares Python 3.11+; Python 3.12 is the retained release
coordinate. A successful inspection ends with:

```json
{"state":"verified","producer_agents":3,"round_1":"blocked","targeted_rework":"verified","round_2":"accepted"}
```

The default demo needs no model account. An optional Codex CLI producer mode
is documented in the [guided demo](oss/flowness-oss-harness/docs/open-alpha-demo.md).

## What is open today — and what is not yet

Flowness is being opened in slices. The repository currently makes the
acceptance kernel runnable and exposes selected event, projection,
orchestration, review, recovery, lock, and worktree mechanisms.

| Surface | Current public status |
| --- | --- |
| FAIL → targeted rework → fresh PASS demo | Runnable Open Alpha |
| Candidate sealing, jury isolation, blocker lineage, trace inspection | Runnable/experimental |
| Ledger Core: append-only decisions, projection freshness, terminal verdicts, bounded tail recovery | Experimental narrow core |
| Selected orchestration, review, recovery, lock, and worktree mechanisms | Inspectable experimental source |
| Interview → design → engineering spec → consensus cognitive pipeline | The lifecycle model and current gaps are documented here. The private runtime has the main objects, CLI, gates, tests, and partial routes; automatic tier routing and engineering-spec → consensus publishing are not closed, there is no organic E2E proof, and this is not in the runnable public slice |
| Production fleet, accounts, credentials, private transcripts, and server operations | Not part of the public source |

That distinction matters: the diagram describes the product Flowness is
building; the demo proves only the public slice it actually runs today. The
next high-value OSS milestone is a real goal-to-accepted-result case that opens
and exercises the cognitive pipeline, rather than another synthetic jury demo.

## Choose your depth

- **I have 10 minutes:** run the [FAIL → rework → PASS demo](oss/flowness-oss-harness/docs/open-alpha-demo.md).
- **I want the big picture:** read the [current architecture overview](docs/architecture.md).
- **I want the work before coding:** inspect the [design × engineering rings](docs/design-engineering-rings.zh-CN.md).
- **I want to challenge the claims:** inspect the [Mechanism Registry](oss/flowness-oss-harness/registries/mechanism-registry-seed-v0.json), [Drift Atlas](oss/flowness-oss-harness/docs/drift-atlas-seed-v0.md), and [benchmark protocol](oss/flowness-oss-harness/docs/benchmark-protocol.md).
- **I want the deep Alpha mechanism views:** open the [D0–D9 Architecture Atlas](oss/flowness-oss-harness/docs/architecture-atlas.md).
- **I used Wow-Harness:** start with the [v0 → v1 migration guide](MIGRATION.md).

## Architecture, progressively

The architecture materials are designed to be entered at different depths:

- **D0–D2:** the problem, goal-to-outcome journey, and lifecycle;
- **D3–D5:** control, execution, evidence, security, mechanisms, and runtime sequence;
- **D6–D8:** deployment/failure domains, authority boundaries, and provenance;
- **D9:** current, experimental, designed-target, and external boundaries.

The [current overview](docs/architecture.md) is the product entry point and
explicitly separates the complete lifecycle model, partial private stages, and
the runnable public slice. The older D0–D9 Atlas remains useful for lower-level
Open Alpha mechanism evidence, but its D1/D2 views predate the reconstructed
design and engineering-spec stages and are not the canonical lifecycle map.

## Repository map

- `oss/flowness-oss-harness/` — runnable Open Alpha package, demo, tests, and evidence contracts;
- `harness/` — selected experimental orchestration, review, recovery, and runtime mechanisms;
- `public-core/flowness-ledger-core/` — the narrow Ledger Core package;
- `docs/` — reader-facing architecture and cases;
- `MIGRATION.md` — Wow-Harness v0 to Flowness v1 history and migration boundary;
- `LICENSE-MATRIX.md` — version and asset license map.

## From Wow-Harness to Flowness

Flowness is the ground-up major-version evolution of Wow-Harness, not an
unrelated project borrowing its history. The repository preserves the v0 Git
history, final legacy tag, contributors, issues, stars, and forks. Those show
the history of the project; they do not by themselves validate the rewritten
v1 implementation.

## Trust and boundaries

`v1.0.0-alpha` is an Open Alpha: runnable and inspectable, not a claim of
production reliability, scale, benchmark leadership, security hardening, or
external adoption. Architecture nodes and claims are labeled as current,
experimental, designed target, blocked, or unknown so a future design cannot
masquerade as shipped behavior.

The public tree excludes credentials, customer material, raw transcripts,
private runtime ledgers, and server/fleet controls. See the
[package scope](oss/flowness-oss-harness/docs/open-alpha-package-scope-v0.md),
[security policy](SECURITY.md), and [license matrix](LICENSE-MATRIX.md).

## Contributing

The most valuable contributions now are not more slogans or more framework
wrappers. They are reproducible cases, adversarial evaluations, broken
evidence links, clearer explanations, clean-room installation results, and
small mechanisms that survive independent review.

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
and [SECURITY.md](SECURITY.md).
