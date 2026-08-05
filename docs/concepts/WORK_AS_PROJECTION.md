# Work as Projection

**Flow**, not Agent or Session, is Flowness's stable unit of control. **Work** is the Flow's stable, addressable projection—the interface through which humans and programs locate, query, and act on it while it continues.

## 1. Why session-first breaks down

Sessions are convenient interaction containers, but poor long-horizon work identities. They are bounded by context, tied to one model process, difficult to reconcile after interruption, and often mix transient reasoning with durable facts.

When a system treats a session as the work, common failure modes follow:

- the task disappears when the session dies;
- each restart reconstructs the world differently;
- a new agent inherits a prose summary instead of authoritative state;
- the latest plan is treated as truth even after assumptions change;
- completion is whatever the final message claims;
- humans must watch sessions because no independent work state exists.

None of this is fixed by swapping in a different container. It requires making the **Flow** itself—not any single session—the persistent thing, and giving it a stable, queryable projection: **Work**.

## 2. The Work / Execution split

Flowness distinguishes:

```text
Work
  addressable projection: goal, state, history, obligations, findings, future

Execution
  one bounded attempt by an agent or deterministic executor
```

One Work may have many Executions. Executions may overlap, fail, be superseded, or be replaced. They cannot erase or redefine the Work by themselves.

Work itself is not immutable. As the Flow it projects branches, recombines, or is corrected, Work can:

- **split**—a Flow forks into independent lines, each earning its own addressable Work;
- **merge**—parallel lines converge back into one Work;
- **be superseded**—a corrected successor replaces a stale Work while preserving the lineage back to it.

None of these operations touch Execution directly. They re-project the Flow's current shape onto the Work surface that represents it.

## 3. A proposed public Work model

The public API should expose a stable projection before attempting to expose every internal object.

```python
@dataclass(frozen=True)
class WorkView:
    work_id: str
    goal_ref: str
    state: WorkState
    as_of_seq: int

    graph_version: str | None
    active_obligations: tuple[str, ...]
    ready_conditions: tuple[str, ...]
    blocked_on: tuple[str, ...]

    active_executions: tuple[str, ...]
    recoverable_executions: tuple[str, ...]
    findings: tuple[str, ...]
    next_actions: tuple[str, ...]

    effect_state: EffectState
    activation_state: ActivationState
    acceptance_state: AcceptanceState
    pending_owner_decisions: tuple[str, ...]
```

This can be a projection over existing events and concepts. It does not require an immediate rewrite of the internal ontology.

## 4. Work state is not one linear enum

A single `todo / doing / done` field cannot represent a real Flow. At minimum, Flowness should expose orthogonal dimensions:

- **progress:** proposed, ready, active, waiting, blocked, reflowing, closed, terminated;
- **freshness:** current, suspect, stale, superseded;
- **effect:** none, built, integrated, activated;
- **acceptance:** unreviewed, blocked, accepted, waived, rejected;
- **liveness:** live, waiting-with-expectation, orphaned, dead-lettered;
- **authority:** unowned, delegated, owner-gated, irreversible-boundary.

This avoids turning “done” into an overloaded lie.

## 5. Work commands

A public CLI should let a human ask questions about the work without opening an agent transcript:

```bash
flowness work show W-42
flowness work next W-42
flowness work explain W-42 --why-blocked
flowness work graph W-42 --at 18420
flowness work history W-42
flowness work evidence W-42
```

A high-value output is:

```text
WORK W-42
state: blocked
freshness: current

agents: none
flow: alive

waiting_on:
  owner decision OD-7

current_graph:
  v7 (supersedes v6)

next:
  compile repair capsule after OD-7

effect:
  built=yes
  integrated=yes
  activated=unknown
  accepted=no
```

## 6. Flow-first acceptance tests

A runtime should not call itself flow-first unless it can pass tests such as:

1. Kill the active agent; the Work remains queryable and has a valid recovery path.
2. Replace the model provider; Work identity and committed facts remain unchanged.
3. Change an upstream decision; affected downstream state becomes suspect and a new graph version appears.
4. Leave no agent running; a waiting expectation can wake the Work when its condition arrives.
5. Produce code without a real consumer; effect remains `built`, not `activated` or `accepted`.
6. Re-run the same Work; old evidence and old verdicts cannot bind silently to the successor version.

## 7. Why this matters beyond software engineering

Flow-first is domain-neutral. In other domains, Work might be a procurement case, a research question, a compliance finding, a manufacturing change, a customer resolution, or a multi-party decision.

The domain ontology changes. The runtime questions remain:

- What persists?
- What is current?
- What is ready?
- What is blocked?
- What authority is required?
- What changed reality?
- What evidence closes the work?

Software engineering is Flowness’s first proving ground because code, tests, repositories, CI, deployment, and review provide unusually rich observable evidence.
