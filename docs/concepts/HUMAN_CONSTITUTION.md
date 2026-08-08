# Human Constitution

> **Human control is infrastructural, not conversational.**

A human should not need to watch every token, approve every tool call, or preserve every session in order to remain in control of an agentic system. Control should be encoded in the substrate that determines what work may form, what actions are legal, what evidence is required, and where autonomy must stop.

Flowness calls that substrate the **Human Constitution**.

This layer is **ming (命)**—the grammar that generates legitimate paths, not a script that prescribes one. It is not a list of outcomes to check off; it is what makes some next steps valid and others illegitimate before any world event ever arrives: goals and anti-goals, ontology and relations, constraints and obligations, what change is legitimate, what evidence can move state, and what counts as accepted.

Strictly speaking, the constitution is the *normative core* of ming — the part with an author. Ming as a whole also inherits the work's irreversible structure: effects already produced, exact versions and relations, accepted commitments, and obligations formed by history. Humans own the part that can be rewritten; the flow carries the part that cannot. (See [Agent as Temporary Form](./AGENT_AS_TEMPORARY_FORM.md).)

## 1. What humans own

Humans remain authoritative over six classes of decisions.

### Goals and anti-goals

What outcome matters, what must not be optimized away, and what trade-offs are unacceptable.

### Ontology and exact identity

What the relevant objects are, how they are distinguished, and which versions or boundaries matter.

### Judgments and counterexamples

Context-dependent decisions that cannot be reduced safely to a universal rule, including known exceptions and disconfirming cases.

### Obligations and red lines

Conditions that must be satisfied before a state transition, and actions the system may not take autonomously.

### Authority and irreversible boundaries

Who may authorize deployment, disclosure, deletion, money movement, external commitment, or other irreversible effects.

### Acceptance and learning promotion

Who accepts a result, and who may promote a repeated pattern into a durable skill, policy, validator, or default route.

## 2. What the system may organize

Within those boundaries, the runtime may determine:

- which capability is needed;
- which agent or deterministic tool should execute;
- how much context is sufficient;
- which work can run in parallel;
- which validator should be applied;
- whether a transient failure should retry;
- whether a Finding suggests repair, replan, re-engineer, redesign, or re-interview;
- when to wait and what event should wake the Flow.

This is **governed self-organization**, not unconstrained autonomy.

## 3. Four control surfaces

### 3.1 Constitutive control

Humans define the field before execution:

```yaml
goals:
  - maintain scheduler availability during pause
anti_goals:
  - do not bypass operator authority
obligations:
  - production activation requires organic invocation evidence
redlines:
  - no direct production write by a model
acceptance_authority:
  - operations-owner
```

### 3.2 Exception control

The system escalates only when a value judgment, missing authority, high-impact ambiguity, or unresolved trade-off prevents safe progress.

A good Owner Inbox item contains:

- the decision;
- why it blocks current work;
- authoritative context;
- options;
- trade-offs;
- a recommendation and its uncertainty;
- what each answer will unlock or invalidate.

### 3.3 Irreversible-effect control

Certain transitions require binding human authority even if every model and test is green. Examples include production deployment, public release, destructive migration, external contractual commitment, and publication of sensitive information.

### 3.4 Evolution control

The system can propose changes to itself, but durable promotion requires evidence.

A candidate rule should move through a lifecycle such as:

```text
proposed → shadow → warning → enforced → retired
```

Promotion should bind:

- the failure history that motivated it;
- precision and recall observations;
- regression fixtures;
- cost and human-load impact;
- rollback path;
- responsible approver.

## 4. Human-in-the-loop is not one thing

The phrase often collapses several distinct relationships:

| Mode | Human role | Risk |
|---|---|---|
| Transcript supervision | watches every execution | does not scale; hides weak infrastructure |
| Approval theater | clicks “approve” without prepared context | transfers liability without real judgment |
| Exception owner | resolves bounded ambiguity | useful when the system prepares the decision |
| Constitutional author | defines goals, rules, authority, acceptance | the primary Flowness model |
| Outcome accepter | confirms the real-world result | required where acceptance is not inferable |
| Evolution governor | promotes learned behavior | prevents uncontrolled self-modification |

Flowness optimizes for the last four, not the first two.

## 5. A measurable governance model

Human control should be evaluated, not merely asserted. Suggested metrics include:

- owner decisions per closed Work;
- median context-reading time per decision;
- percentage of escalations that were actually blocking;
- recommendation acceptance rate and reversal rate;
- false escalation and missed escalation rate;
- time spent in irreversible-effect gates;
- number of durable rules promoted, rolled back, or retired;
- incidents caused by authority substitution;
- percentage of Work that completes without transcript supervision.

The goal is not zero human involvement. It is **high-leverage human involvement**.

## 6. The operating principle

> People define the world in which autonomy is legitimate. Work calls capabilities inside that world. The system returns to people when it reaches a value, authority, or irreversible boundary it cannot lawfully cross.

Or, more compactly:

> **Human out of the session, never out of the constitution.**

Human Constitution is **ming (命)**; World State is **yun (运)**. Where the two meet, the runtime compiles one temporary agent—the Execution Assembly.
