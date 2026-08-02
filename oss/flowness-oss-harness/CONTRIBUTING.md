<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Contributing to the Flowness Open Alpha

This repository contains the experimental Flowness Open Alpha, an
evidence-first multi-agent Harness. Contributions are welcome when they
preserve the difference between evidence-bound behavior, experimental source,
design targets, and unknowns.

## Before opening a change

1. Keep the change focused and explain the failure or user need it addresses.
2. Add or update the closest automated test.
3. Label capability statements as `stable`, `experimental`, `design target`, or
   `unknown`; tests and prose must not promote a maturity state by implication.
4. Do not commit credentials, transcripts, customer material, personal paths,
   private runtime records, or rights-unknown copied assets.
5. Identify any third-party code, text, data, image, or generated asset and link
   the source and license in the pull request.

## Pull-request evidence

A useful pull request includes the exact behavior changed, commands run, results,
known limits, and any public claim or diagram affected by the change. A green unit
test proves only its tested contract; production and external-effect claims need
their own evidence.

The Open Alpha maintainers may ask for targeted rework when an independent judge
finds a blocker. Keep the same blocker identifier through the fix and retest so
the history remains traceable.

By contributing, you represent that you have the right to submit the
contribution under the license assigned to its path by the repository license
matrix. Historical private-staging evidence does not expand the current public
scope, promote an experimental claim, or authorize a release action.
