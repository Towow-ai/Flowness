# Assurance Kernel Demo

**Status:** `[RUNNABLE]` in the current public Open Alpha.  
**Role in the product story:** a narrow, reproducible proof of independent acceptance and targeted rework—not the definition of the complete Flow runtime.

## What it demonstrates

The demo establishes that a candidate can be:

1. produced by isolated producer roles;
2. sealed to a content-bound identity;
3. evaluated by judges separated from producers;
4. blocked by a mandatory Finding;
5. repaired through targeted rework;
6. re-evaluated as a successor candidate;
7. accepted only through a fresh verdict;
8. inspected independently from the producer’s narrative.

## Run it

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

Expected verified summary:

```json
{"state":"verified","producer_agents":3,"round_1":"blocked","targeted_rework":"verified","round_2":"accepted"}
```

## What it does not demonstrate

The current demo does not by itself establish:

- persistent Work across agent loss;
- dynamic graph compilation from live Work state;
- a complete public goal → design → engineering → execution → reality path;
- organic production activation;
- general cross-domain Flow behavior;
- superiority over other frameworks;
- empirical reduction of cost or failure rate on external tasks.

Those remain separate claims with separate evidence requirements.

## Why keep it

Independent acceptance is still load-bearing. A Flow that moves quickly but allows producers to define their own proof can remain wrong while appearing healthy.

The correct packaging is:

> The Assurance Kernel protects one important boundary of a Flow: a candidate cannot erase its Finding, reuse an old verdict, or become accepted merely because its producer says it is fixed.

## Naming guidance

Use:

- **Assurance Kernel Demo**
- **Independent Acceptance Demo**
- **Targeted Rework Demo**

Avoid presenting it as:

- the full Flowness lifecycle;
- the Flow Engineering demo;
- proof of a general autonomous software factory.
