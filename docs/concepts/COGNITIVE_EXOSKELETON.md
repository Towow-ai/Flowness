# Cognitive Exoskeleton

A personal or organizational Harness becomes valuable when it does more than automate tasks. It should preserve and compound the owner’s way of seeing, deciding, verifying, and learning.

Flowness calls that accumulated executable structure a **Cognitive Exoskeleton**.

## 1. What compounds

A Cognitive Exoskeleton may contain:

- goals and anti-goals;
- domain ontology and exact object identities;
- recurring problem patterns;
- human judgments and their context;
- counterexamples and exceptions;
- design principles and rejected alternatives;
- engineering decisions and reopen conditions;
- obligations and policies;
- skills and capability contracts;
- validators and acceptance criteria;
- failure history and repair patterns;
- evidence and confidence;
- promotion and supersede history.

This is not merely “memory.” Memory remembers what was said. A Cognitive Exoskeleton changes what the system does next.

## 2. Why a personal Harness can be more valuable than a universal one

General frameworks optimize for broad applicability. But a Harness becomes stronger as it learns:

- what this person or organization actually values;
- which shortcuts are acceptable;
- which risks are intolerable;
- how quality is recognized;
- what repeated mistakes look like;
- which evidence is trusted;
- when a human must be present;
- how work moves through a particular environment.

That creates a tension: deeper fit increases usefulness while reducing universal portability.

Flowness therefore should be presented as:

> **A reference runtime and mechanism kit for building your own work-centered Harness—not a claim that one universal configuration fits every team.**

## 3. Judgment is not a universal rule

A durable JudgmentCase should preserve context rather than flatten a human decision into “always do X.”

```yaml
judgment_id: J-104
question: Should an incomplete benchmark block release?
context:
  impact: high
  reversibility: low
  deadline: flexible
judgment:
  decision: block
  reason: performance claim is launch-critical
counterexamples:
  - low-impact internal preview with claim removed
supersedes: J-088
owner: Nature
```

Retrieval should surface:

- direct analogies;
- indirect analogies;
- structural analogies;
- counterexamples;
- later superseding judgments.

The retrieval layer should have high recall but low authority. It helps the system remember relevant thinking; it does not decide that a past judgment applies automatically.

## 4. From trace to durable structure

A healthy learning loop is:

```text
execution trace
→ recurring failure or useful pattern
→ candidate insight
→ counterexample search
→ shadow rule / skill / validator
→ measured trial
→ human promotion
→ future context and execution change
```

This avoids two common mistakes:

- **no learning:** every session repeats the same reasoning from zero;
- **uncontrolled learning:** one accidental success becomes a permanent global rule.

## 5. Models are replaceable

The model provider, model version, and agent shell will change. A Cognitive Exoskeleton should remain portable because its durable assets are explicit objects with versions, provenance, and tests.

> **Models are replaceable. Your judgment should compound.**

This is the long-term reason to build one’s own Harness from first principles. Even when a later framework supplies better execution primitives, the accumulated ontology, judgments, obligations, validators, and failure knowledge remain valuable.

## 6. What should stay human

Not every thought should become infrastructure. Keep human presence when:

- the situation is value-laden and novel;
- the cost of false generalization is high;
- the decision depends on tacit interpersonal context;
- authority cannot be delegated;
- counterexamples are sparse;
- the concept is still unstable;
- measurement would create perverse incentives.

A good Cognitive Exoskeleton does not replace the person. It preserves their scarce attention for the parts where lived judgment is still essential.
