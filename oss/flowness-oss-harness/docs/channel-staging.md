# Channel staging

This harness stages packages; it does not publish them. Every channel keeps
`publish_requires_owner_approval=true`.

## Stages

| Stage | Purpose | Eligible surfaces | Required proof |
| --- | --- | --- | --- |
| S0 private research | Discover mechanisms, failures, claims, and language | Private workspace only | Sealed snapshots and source boundary |
| S1 alpha preparation | Let technical adopters reproduce one bounded path | GitHub repository, release notes, minimal docs/demo staging | G0-G4 Pass; G5 may remain advisory |
| S2 beta expansion | Prove broader operation and external adoption | Website, long-form article, benchmark page, video, community onboarding | Beta stage profile Pass, external reproduction, versioned eval |
| S3 1.0 launch | Coordinated public release | GitHub, website, Chinese/English articles, video, talks, community packages | 1.0 profile Pass, owner approval, rollback and response readiness |

## Asset tiers

- Tier A — release truth: README, license, security, contributing, governance,
  support scope, install, quickstart, compatibility, upgrade/rollback,
  changelog, claim registry, and release notes.
- Tier B — understanding: D0-D9 Atlas, terminology, FAQ, fit/no-fit, mechanism
  deep dives, worked success/failure cases.
- Tier C — proof: canonical demo, raw benchmark package, evaluation report,
  external reproduction, cost/latency/token disclosure.
- Tier D — distribution: website page, WeChat/Zhihu/Juejin adaptations, video,
  talk kit, social cards, community onboarding.

Tier D cannot precede accepted Tier A-C source nodes. A whitepaper is optional:
publish it only when it contains a falsifiable model, versioned architecture,
reproducible experiments, related work, and explicit limitations. Otherwise
call it a technical report or design note.

## Channel contracts

Each package records channel, audience, language, release stage, source node
IDs, candidate snapshot, asset hashes, reviewer IDs, CTA, staging state, and
owner approval. Analytics are separated into attention, read, install,
first-success, retention, issue, external contribution, and adoption. Attention
alone is not success.

GitHub remains the canonical public engineering surface. Website and long-form
articles provide layered explanation. Video demonstrates state change and
failure/recovery. Community channels route feedback and contributors back to
versioned issues and source documents.
