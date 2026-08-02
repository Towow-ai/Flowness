<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Contributing to Flowness

Thank you for helping make multi-agent engineering easier to inspect and
accept. Start with a small, reproducible change and preserve the evidence that
supports it.

## Before opening a change

1. Search existing issues and explain the user-visible problem, failure mode,
   or missing mechanism.
2. State whether the change affects code, tests, a public claim, architecture,
   a demo, or channel material.
3. Add or update the closest test. A document-only statement is not proof that
   a runtime mechanism exists.
4. Preserve failures and critical Unknowns. Do not clear them by averaging
   scores or rewriting the explanation.
5. Do not include secrets, customer data, raw Transcripts, private server
   configuration, account/quota records, or material with unclear rights.

## Development setup

The accepted Alpha coordinate is Linux aarch64 with CPython 3.12. Create a
fresh environment from the repository root:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r harness/open-alpha-requirements.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation \
  -e ./harness -e ./oss/flowness-oss-harness \
  -e ./public-core/flowness-ledger-core
```

Run the test nearest your change. For changes to the public distribution
boundary, also run the same targeted suite declared in
[`ci.yml`](.github/workflows/ci.yml).

## Pull requests

A pull request should contain one coherent change, its verification command,
the observed result, and every remaining limitation. If behavior changes,
update the dependent README, demo, architecture, and Claim/Drift entries in the
same change. A new public claim needs evidence at the maturity level it names.

Contributor submissions are accepted under the license applying to the
modified path in [LICENSE-MATRIX.md](LICENSE-MATRIX.md): Apache-2.0 for code and
operational assets, or CC-BY-4.0 for explanatory documentation and media,
unless a more specific file notice applies.

Use the issue tracker for defects and proposals. Use the private process in
[SECURITY.md](SECURITY.md) for vulnerabilities.
