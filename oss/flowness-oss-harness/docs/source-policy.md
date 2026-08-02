# Source policy

`config/source-boundaries.json` is deny-by-default. It distinguishes evidence
that can support a current Flowness claim from material that can only guide
research or narrative structure.

## Evidence hierarchy

For implementation claims, prefer authoritative postconditions, accepted event
or state records, executable tests, runtime traces, and sealed code snapshots.
Architecture or prose can explain implementation but cannot prove it.

Public first-party sources may support facts about another project. Repository
README files support positioning and documented interfaces; they do not prove
that a mechanism works. Source, tests, releases, installation, issue history,
and reproducible execution are recorded separately.

Raw transcripts, credentials, customer material, live private ledgers, broad
repository copies, and unreviewed imports are denied. Approved transcript
excerpts need confirmed origin, redaction, content hash, and owner permission.

## Independent synthesis boundary

An independently compiled study note may reveal useful structure, sources, or
questions. It is not an official publication merely because it is based on
official materials. Every number and conclusion must be checked against a
primary source and then re-evaluated for Flowness.

The Graph Engineering study note reviewed for this project explicitly describes
itself as independently compiled and not affiliated with or endorsed by
Anthropic. It may influence narrative grammar only. No diagram, wording,
conclusion, metric, or product claim is imported into Flowness without separate
source, license, and evidence review.
