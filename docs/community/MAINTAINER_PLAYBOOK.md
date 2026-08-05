# Maintainer Playbook

## 1. Maintain one public truth

Every release must bind:

- source commit;
- package versions;
- demo or benchmark inputs;
- claim/evidence register;
- SHA-256 manifest;
- known limitations.

A launch post must reference a release-bound evidence page, not a mutable local folder.

## 2. Triage by contribution type

| Type | Home | Required response |
|---|---|---|
| Reproducible defect | Issue | acknowledge, request minimal fixture, assign status |
| Failure fixture | Issue form | verify clean-room reproduction and taxonomy |
| Concept challenge | Discussion | restate strongest objection; link evidence |
| Mechanism proposal | Issue/RFC | define contract, failure domain, tests, maintenance cost |
| Benchmark provider | Issue form | freeze inputs, authority, run protocol, expected artifacts |
| Security report | private security channel | follow SECURITY.md; do not ask for public disclosure |

## 3. Claim promotion

A claim moves through:

```text
proposed → designed → inspectable → runnable → independently reproduced
```

Dogfood is an orthogonal evidence source, not a substitute for public reproduction.

Before promotion:

- identify the exact claim;
- freeze the release coordinate;
- provide positive and negative cases;
- state known scope and failure modes;
- add a regression test or conformance artifact;
- update the claim register and public copy in the same pull request.

## 4. Contribution review

Review in this order:

1. Does the contribution preserve Flow-first semantics (Work as addressable projection, not a god object)?
2. What authoritative object and version does it read?
3. What event or state change does it produce?
4. Who or what consumes the output?
5. What wakes it, times it out, or dead-letters it?
6. How is failure observed and routed?
7. What evidence proves the claimed effect?
8. What maintenance burden is introduced?

A locally green module without a declared consumer or runtime path is incomplete.

## 5. Community rhythm

- Weekly: issue triage and evidence-link check.
- Biweekly: one public Flow Clinic on a real failure.
- Monthly: one mechanism release with a fixture and boundary.
- Quarterly: claim-register audit, roadmap prune, and evidence snapshot.

Avoid launching a Discord server until GitHub Discussions can no longer support the actual participation volume.

## 6. Disagreement protocol

When maintainers disagree:

- separate fact, interpretation, value judgment, and implementation preference;
- identify the object/version under discussion;
- state what evidence would change each position;
- prefer a bounded experiment over rhetorical convergence;
- preserve rejected alternatives when they encode a future condition.

## 7. Do not optimize for vanity

Stars, impressions, and launch rankings are useful signals but not acceptance criteria. The project should track:

- clean-room successful installs;
- external fixture submissions;
- independently reproduced claims;
- useful mechanism reuse;
- qualified contributors returning;
- defects found before the maintainer did;
- organic examples that survive release binding.
