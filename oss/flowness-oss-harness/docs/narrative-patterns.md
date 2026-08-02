# Narrative patterns

This guide borrows a communication sequence observed in an independently
compiled Graph Engineering study note. It does not copy that note's expression,
diagrams, examples, conclusions, or product concepts. The note is not an
Anthropic whitepaper or endorsement; all conclusions and numbers require
independent verification.

## Recommended sequence

1. First screen: label the artifact precisely — working note, technical report,
   release guide, or whitepaper — and disclose compiler, date, affiliation, and
   endorsement status.
2. Enter through one concrete failure scenario instead of an abstract category.
3. State fit, no-fit, and the adjacent approach boundary.
4. Name no more than three contributions, each linked to a claim ID and
   falsifier.
5. Show one end-to-end overview with stage responsibilities and feedback loops.
6. Expand each stage through schema, prompt or policy, implementation pointer,
   failure mode, evidence, and quantitative result.
7. Map the mechanism into relevant single-agent and multi-agent patterns,
   including where parallelization is inappropriate.
8. Show the evaluation feedback loop and the path from finding to rework,
   retest, adjudication, and acceptance.
9. Address cost, scaling, monitoring, recovery, trust, and operator boundaries.
10. Close with related work, source boundary, glossary, worked success and
    failure examples, production checklist, and known unknowns.

The visual progression is `system overview → local mechanism diagrams → tables
and evidence → worked case`. Each visual follows the D0-D9 Atlas rules and says
what it cannot prove.

## Claim discipline

Flowness only borrows this communication grammar. Every statement is generated
from the Flowness content graph and returns to a candidate claim, mechanism,
evidence, benchmark, and limitation record. External numbers are never
transferred as Flowness efficacy claims. A citation to an official source proves
only that source's scoped observation.
