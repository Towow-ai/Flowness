# Assurance Kernel Demo

The current public Open Alpha contains a deterministic proof of one load-bearing part of Flowness: a candidate cannot become accepted merely because its producer says it is done.

## What it demonstrates

`[RUNNABLE]`

- isolated producers;
- content-bound candidates and evidence;
- producer/judge separation;
- a mandatory blocker that survives rework;
- targeted correction rather than unconditional full rerun;
- fresh judgment of the successor candidate;
- an independently inspectable trace.

## What it does not demonstrate

The demo is not yet proof of:

- a persistent first-class Work object across killed Agent sessions;
- runtime compilation of multiple graph versions around changing Work state;
- an organic public goal → accepted outcome on a new repository target;
- automatic design or engineering-spec reflow;
- production scale, security hardening, or benchmark superiority.

Those are tracked separately in the claim register and roadmap.

## Run

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

Expected terminal state:

```json
{"state":"verified","producer_agents":3,"round_1":"blocked","targeted_rework":"verified","round_2":"accepted"}
```

## Why this proof remains useful

A Flow runtime needs more than continuity. It also needs credible transition and closure rules. The Assurance Kernel shows that one producer’s confidence cannot erase a mandatory Finding, and that a successor must be evaluated against fresh, bound evidence.

The next category-defining proof is the [Work Outlives Agents Hero Demo](demos/HERO_DEMO_SPEC.md).
