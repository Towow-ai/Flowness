<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Bilingual parity contract

Status: **experimental Open Alpha validation contract**. The contract was
introduced during private staging; references below to
`local_unsealed_candidate` are preserved historical fixture vocabulary, not
the current repository or release state.

`bilingual-parity/v1` is a narrow, fail-closed preservation check for a
bounded EN/ZH pair.  It is not a translation evaluator and it cannot convert a
candidate into a release.

For every mapped claim unit, the contract binds both source files by SHA-256
and requires each language to retain all of the following:

- the claim fragment;
- the fixture's historical `local_unsealed_candidate` state label and its
  corresponding bounded-candidate wording;
- every named limitation;
- an explicit no-fit statement; and
- a bounded next-step CTA.

The verifier rejects a changed source hash, a changed bound Content Graph byte
sequence, a missing English or Chinese fragment, a missing historical
local-unsealed limitation, a missing limitation/no-fit/CTA, or
production-promotion wording.
It also cross-checks the precise EN/ZH assets against the hash-bound named
`content-graph/v3` localization pair.  That pair must remain `unreviewed` with
no review IDs.  Thus an asset change must be deliberately rebound through
V2 → V3 → parity contract, rather than making an unchanged graph path look
current.

Two reviewer slots are intentionally fixed as `pending`:

1. `bilingual_semantics` judges whether the wording means the same thing;
2. `claim_boundary` judges scope, limitations and no-fit preservation.

No machine output can set those slots to approved.  A future review record
needs its own identity and evidence contract; this preservation checker never
manufactures one. Its output is bounded review evidence only and has no
publication, scheduling, send, release, or production-promotion capability.

The initial bounded pair is the Ledger candidate FAQ in
`registries/ledger-faq-bilingual-parity-candidate-v0.json`.  It maps only two
candidate claims.  It does not claim that every Flowness document is bilingual
or semantically reviewed.
