# Agent as Temporary Form

> The flow is primary. Agents are temporary forms of execution.

Every other document in this set describes what persists—Flow, Work, Human Constitution, Cognitive Exoskeleton—or what gets compiled around what persists—Harness, Loop, Graph, Context Capsule, Execution Assembly. None of them says, directly, what an agent *is*. This document does.

---

## 1. What an agent is

An agent is not a role in an org chart, a named identity, or a persistent process. An agent is **agency temporarily taking form**: capability, compiled and given shape for the duration of one action, on behalf of a Flow that outlives it.

Flow is first-class; Work is its addressable interface; the agent is what the runtime becomes, briefly, to act on the Flow's behalf.

An agent exists only inside the window between compilation and dissolution. Before that window, there is no agent—only a Flow waiting for the conditions under which one can legitimately form. After that window, there is no agent either—only the Flow, carrying forward whatever the agent left behind.

---

## 1a. Model, Execution Assembly, Agent — three different things

A common collapse must be prevented early: *the model is not the agent.*

```text
Model / Executor
  a capability provider — reasoning or deterministic execution.

Execution Assembly
  the temporary structure compiled for one action:
  context, tools, permissions, obligations, scope,
  validators, graph position, resource and authority boundaries.

Agent (an instance of agency)
  what that assembly becomes at the moment it acts:
  a bounded occurrence of agency, not a durable actor.
```

The model only provides capability. The assembly gives that capability a
lawful, bounded world. The agent is what happens when the two are compiled
together and act — and one assembly may even hold several executors at once,
in which case the agent is not any single model inside it.

This is why "agent" here never means "a short-lived person." It is not a
persistent entity with a reduced lifespan; it is **an occurrence of agency**.

## 2. Ming and yun: the two things that meet

Every temporary agent is compiled from exactly two ingredients.

**Ming (命)—the grammar.** Goals and anti-goals, ontology and relations, constraints and obligations, what change is legitimate, what evidence can move state, what counts as accepted. Not a script—the grammar that generates valid paths. This is the [Human Constitution](./HUMAN_CONSTITUTION.md).

**Yun (运)—the moment.** The present events, world state, resources, available capabilities, permissions, exposed risks. This is the current World State.

Neither one alone can produce an agent. Ming without yun is a rulebook with nothing to apply it to. Yun without ming is raw circumstance with no legitimate path through it. An agent exists only where the two meet.

---


One precision matters here. The Human Constitution is the **normative core**
of ming — the part a person owns, interprets, and may revise. But ming is
broader than what humans wrote down. A flow's lawful paths are also shaped by:

- effects that have already happened and cannot be recalled;
- exact objects, versions, and the relations between them;
- dependencies and commitments already accepted;
- obligations accumulated through history;
- causal structure that no one may simply rewrite.

So: **ming = the Human Constitution plus the inherited, irreversible
structure of the work itself.** The constitution is the part with an author;
the rest is the part with a history. Yun remains the present moment —
events, resources, capabilities, permissions, risks, and timing.

## 3. The compile loop

```text
ming (grammar)  +  yun (moment)
        ↓
      compile
        ↓
  temporary agent
        ↓
        act
        ↓
      dissolve
        ↓
 provenance remains
```

Compilation here is not a metaphor—it is the same operation described elsewhere in this set as assembling an Execution Assembly: an executor, a Context Capsule, tools and capabilities, a graph or ready-set, policies and obligations, validators and gates, resource and authority boundaries, and an evidence contract. What ming and yun compile *into* is that assembly; the agent is the executor at its center, temporarily animated by it.

When the action completes—successfully, or with a Finding that routes it elsewhere—the agent dissolves. It does not retire, get reassigned, or wait idle for its next task. It simply stops existing. What remains is not the agent, but what the agent left in the Flow's history: the path taken, the evidence produced, the state it moved to, and any judgment the next compilation can inherit.

---

## 4. The declaration

Agent 无名、无形、无姓。
它不是组织中等待被管理的一个人，而是行动能力在某一时刻的一次成形。
目标、对象、关系、义务与判断构成事情的命脉；事件与环境形成当下之运。
Agent 应运而生，完成一次转化，然后消失。
留下的不是人格，而是路径、证据、状态，以及能够被下一次行动继承的判断。

An agent has no name, no fixed form, no lineage.
It is not a person in an org chart waiting to be managed; it is capability taking shape at a moment in time.
Goals, objects, relations, obligations, and judgments form the enduring thread of the work—its ming (命), the grammar. Events and circumstances form the present conditions—its yun (运), the moment.
Where the two meet, an agent takes form, completes one transformation, and dissolves.
What remains is not a personality, but the path, the evidence, the state—and the judgment the next action can inherit.

---

## 5. What compounds and what doesn't

> 主体不持久，溯源必须持久。执行即生灭，判断可累积。
> The actor does not persist; provenance must. Execution is born and dies; judgment compounds.

This is the operational consequence of §§1–4. If an agent leaves nothing durable behind, its dissolution is a loss. Flowness's answer is that the agent was never the durable thing to begin with—the Flow is, and the Flow's history absorbs what the agent produced. Every dissolved agent should leave the Flow strictly more informed than it found it: a committed effect, a recorded Finding, an updated obligation, a judgment the next compiled agent can retrieve instead of rediscovering from zero.

---

## 6. What this is not

- **This is not a claim that agents are unreliable.** Temporary form describes lifecycle, not quality. A well-compiled agent can be fully trustworthy for the one action it exists to take; "temporary" is not a euphemism for "unstable."
- **This is not an argument against persistent-session tools.** Long-running sessions, IDEs, and interactive shells remain useful *harnesses*—environments that let a sequence of temporary agents act well. What does not persist is the agent's identity across unrelated actions; the session is infrastructure, not the durable subject.
- **This is not a way to remove provenance—it is the opposite.** Because the agent itself leaves nothing behind, provenance is not optional decoration; it is the only mechanism by which a dissolved agent's work becomes usable by anything that comes after it. Every action a temporary agent takes must still be bound to exact evidence, exact versions, and an exact path back to why it happened. Provenance is a hard requirement precisely *because* the actor is not around to ask.

---

## 7. Where this connects

- **Execution Assembly**—what ming and yun compile into. See the whitepaper, [§4.4 Execution Assembly](../whitepaper/flow-engineering-whitepaper.en.md).
- **Provenance**—durable truth, bounded reads, guarded writes, recoverable projections. See [Contributor Map, L0 — Flow Kernel](../architecture/CONTRIBUTOR_MAP.md).
- **Ming**—the grammar an agent is compiled against, and who owns it. See [Human Constitution](./HUMAN_CONSTITUTION.md).
