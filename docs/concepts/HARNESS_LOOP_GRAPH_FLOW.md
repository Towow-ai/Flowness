# Harness, Loop, Graph, and Flow

This document explains four related engineering abstractions without treating them as fashion-driven replacements.

## The compact model

| | One execution unit | The whole execution system |
|---|---|---|
| Structure and operating conditions | **Harness** | **Graph** |
| Dynamics through time | **Loop** | **Flow** |

The table is a teaching aid, not a formal decomposition. In practice, each layer overlaps with the others. Its value is that it makes the progression intuitive.

---

## 1. Harness Engineering

A Harness is the operating environment that turns a model invocation into an agent capable of sustained action.

It typically includes:

- context construction;
- tools and sandbox;
- permissions and policies;
- memory and state;
- checkpoints and recovery;
- model selection;
- completion contracts;
- validators and feedback;
- user interaction.

The core question is:

> **What must surround one agent so that it can act usefully and safely?**

A strong model with a weak Harness may fail because it sees the wrong files, uses the wrong tool, loses state, has no test oracle, or declares completion without effect.

---

## 2. Loop Engineering

A Loop is one execution unit unfolding through time.

```text
observe → reason → act → read back → evaluate → correct → act again
```

Loop Engineering decides:

- when to continue;
- when to retry;
- what feedback to expose;
- when to stop;
- when to ask a human;
- when to change strategy;
- how to learn from traces.

The core question is:

> **How does an execution unit use feedback to keep improving its next action?**

A Loop may live inside one node of a larger Graph.

---

## 3. Graph Engineering

A Graph organizes multiple execution units in space.

```text
node → edge → branch → fan-out → fan-in → gate → recovery path
```

Nodes may be deterministic programs, model calls, tools, full agents with their own loops, validators, or human decisions. Edges describe possible transitions and dependencies. State moves through or is shared by the graph.

The core question is:

> **How should multiple capabilities be connected, ordered, parallelized, and coordinated?**

Graph Engineering is more expressive than a simple linear workflow, especially when transitions can be dynamic.

---

## 4. Flow Engineering

Flow Engineering changes the primary subject.

Instead of starting from an agent or a pre-existing graph, it starts from the Flow's current state, read through its addressable projection, **Work**, and asks:

> **Given what this work currently is, what must happen next—and what execution structure should exist now?**

A Flow is the work moving through a succession of execution assemblies.

```text
Work state S0
  → compile Graph G0
  → execute
  → produce new facts / findings
Work state S1
  → invalidate part of G0
  → compile Graph G1
  → wait for owner decision
Work state S2
  → compile Graph G2
  → execute and activate
Work state Closed
```

This yields three important distinctions:

1. **The Graph can change while the Flow remains the same.**
2. **The Agent can disappear while the Flow remains alive.**
3. **The Loop can complete locally while the Flow remains globally incomplete.**

---

## 5. The hierarchy is not `Harness < Loop < Graph < Flow`

A misleading explanation presents these concepts as generations where the newest replaces the others. Flowness instead uses the following relationship:

```text
Harness equips an execution unit.
Loop drives its temporal feedback.
Graph organizes multiple execution units.
Flow keeps the work moving across changing graphs.
```

Or, in runtime terms:

```text
The Harness runs the execution.
The Loop updates the attempt.
The Graph structures the current assembly.
The Flow persists across all of them.
```

Every serious Flow still needs Harnesses, Loops, and Graphs.

---

## 6. Why Flow is easier to understand than a formal systems definition

The word **flow** carries a natural set of questions:

- What is flowing?
- Where is it now?
- What is blocking it?
- Who or what can receive it?
- What condition makes it continue?
- Where did it branch?
- Why did it return?
- Did it actually arrive?

Those questions map directly to engineering objects:

| Natural question | Engineering object |
|---|---|
| What is flowing? | Flow identity and goal |
| Where is it now? | state projection and watermark |
| Why is it blocked? | unmet condition / obligation / owner decision |
| Who can receive it? | capability and consumer |
| What makes it continue? | readiness rule / event / expectation |
| Why did it return? | Finding and Reflow route |
| Did it arrive? | effect, activation, and acceptance evidence |

The simple metaphor remains useful because it can be made operational.

---

## 7. A practical example

Suppose the goal is:

> Add a safe pause mechanism to a production scheduler.

### Harness view

What context, tools, permissions, tests, and sandbox does the coding agent need?

### Loop view

How does the agent implement, run tests, inspect failures, and revise?

### Graph view

How do design, implementation, security review, integration test, and operator approval connect?

### Flow view

What is the current state of the work? Is the design still valid? Has the code been wired into the production consumer? Does the operator path invoke it organically? If the security review discovers a new obligation, which downstream objects become stale and what graph should now be compiled?

The Flow view explains why local success can coexist with global non-completion.

---

## 8. The Flowness reversal

Most systems organize work around the executor:

```text
choose agent → give context → let agent work → preserve handoff
```

Flowness organizes executors around the work:

```text
read persistent work state
→ compile the needed context, capabilities, graph, and gates
→ summon an executor
→ commit its effects back into the work’s world
```

The shortest expression is:

> **Work persists. Agents assemble.**

And the strongest test is:

```text
agents: none
flow: alive
```
