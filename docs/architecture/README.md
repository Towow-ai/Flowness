# Flowness Architecture

Flowness should not be explained with one diagram. Different readers are asking different questions.

| Reader | First question | Recommended view |
|---|---|---|
| New visitor | What changed? | 5-second category diagram |
| Builder | How does one Work move? | 30-second runtime diagram |
| Owner / executive | Where is human control? | Human Constitution diagram |
| Technical decision-maker | What are the major runtime layers? | Five-layer architecture |
| Contributor | Where does code live? | L0–L3 contributor map |
| Software engineer | What lifecycle does the first profile use? | High-Assurance SWE Flow Profile |
| Auditor / researcher | What facts, claims, and gates are binding? | mechanism registry, claims register, and Failure Atlas |

The canonical visual assets live in [`docs/assets/diagrams/`](../assets/diagrams/); they are generated, versioned, and updated together with the documents that embed them.

---

## 1. Category view — 5 seconds

**Question:** What is Flowness?

```text
Human goals · rules · boundaries
               ↓
      Persistent Work
      state · history · future
               ↓ compile
Agent · Context · Tools · Graph · Gates
               ↓ act
 Effects · Findings · New World State
               ↺
```

Message:

> **Work persists. Agents assemble.**

This view must not show dozens of modules. The stable visual anchor is the constitutional boundary (命 — goals, rules, boundaries); Work is a readable, addressable panel inside it, and execution structures assemble temporarily around the Work panel.

---

## 2. Runtime view — 30 seconds

**Question:** How does a Flow actually run?

```text
Event / intent
→ project current world
→ update persistent Work state
→ compile capsule, capabilities, graph, obligations, and validators
→ execute
→ commit, verify, activate, or reject
→ produce new event
→ continue / wait / branch / merge / reflow / close
```

Message:

> Flow is not messages moving between agents. It is work moving through changing execution assemblies.

---

## 3. Human governance view

**Question:** Does autonomy remove people?

```text
Human Constitution
  goals · ontology · judgments · obligations
  authority · red lines · acceptance
                 ↓
Autonomous operating region
  work forms contexts, graphs, and executions
                 ↓
Exception and owner-decision surface
                 ↓
Irreversible-effect boundary
                 ↓
Evidence-backed promotion and learning
                 ↺
```

Message:

> **Human control is infrastructural, not conversational.**

---

## 4. Five-layer product architecture

See [FIVE_LAYER_ARCHITECTURE.md](FIVE_LAYER_ARCHITECTURE.md).

```text
1. Human Constitution
2. Persistent Work State & Truth
3. Flow Compiler
4. Flow Runtime
5. Reality & Assurance
```

This is the preferred product-level architecture. It is not required to match the physical folder tree one-to-one.

---

## 5. Contributor architecture

See [CONTRIBUTOR_MAP.md](CONTRIBUTOR_MAP.md).

The current repository can be explained as:

```text
L3  Human control surface
L2  Flow runtime and maintenance
L1  Semantic and governance mechanisms
L0  Flow kernel
External world  repositories, CI, deployment, people, effects
```

---

## 6. Domain profile

See [SWE_FLOW_PROFILE.md](SWE_FLOW_PROFILE.md).

The visible lifecycle from problem through acceptance is Flowness’s first high-assurance software-engineering profile. It is not the universal definition of Flow. A small reversible change may legitimately compile a much shorter path.

---

## 7. Diagram rules

Every architecture diagram should follow these rules:

1. **The constitutional boundary is visually stable.** Work is a readable, addressable panel inside that boundary — it can split, merge, or be superseded. Agents and graphs may appear, disappear, or change.
2. **Truth and evidence are not the same plane.** Persistent work truth feeds all projections; assurance protects transitions and closure.
3. **Graph has a version or watermark.** Never depict it as timeless truth.
4. **Human control appears as constitutive boundaries and prepared decisions, not constant supervision.**
5. **Reality is outside the agent runtime.** Code, deployment, organic use, and acceptance are distinct.
6. **Unknown and waiting are valid states.** Do not force every route into green success.
7. **Status labels are visible.** Diagrams must distinguish runnable, inspectable, dogfood, designed, and open-question components.

---

## 8. What not to draw

Avoid:

- a robot swarm as the main character;
- one giant permanent DAG;
- a fixed conveyor-belt pipeline as the system’s ontology;
- a courtroom or jury as the product identity;
- a human staring at every session;
- a glowing black-box “AI brain” at the center;
- a single green “done” node after tests pass.

Those visuals misplace the center of gravity.
