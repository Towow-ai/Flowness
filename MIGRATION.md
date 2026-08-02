<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Migrating from Wow-Harness v0.x to Flowness v1

Flowness is a ground-up major-version evolution of Wow-Harness, not a
backward-compatible patch. The repository history is continuous; the runtime,
CLI, configuration, package names, and acceptance model are not.

## Legacy references

The repository transition preserves the final pre-Flowness Wow-Harness state
at both:

- branch: `legacy/wow-harness-v0`
- tag: `wow-harness-v0-final`

Those references retain the original MIT-licensed bytes and contributor
history. They are read-only historical support surfaces, not maintained v1
branches.

## Repository transition

The major-version release sequence is:

1. create the legacy branch and tag from the pre-Flowness head;
2. import the rights-cleared Flowness tree in one reviewable replacement
   commit, without force-pushing or rewriting history;
3. transfer the repository to `Towow-ai`, rename it to `Flowness`, and verify
   old URL, clone/fetch, fork, issue, pull-request, and link redirects;
4. enable and test GitHub Private Vulnerability Reporting;
5. publish `v1.0.0-alpha` only after all checks above pass.

Historical stars, forks, issues, and pull requests remain useful lineage. They
do not establish adoption, reliability, or validation of the rewritten v1
implementation.

## Breaking changes

### Packages and CLI

Do not assume a Wow-Harness command or Python import maps one-to-one to
Flowness. The v1 public entry points are:

| Surface | Public entry |
| --- | --- |
| Selected canonical engine | `flowness-harness` |
| Deterministic Harness demo | `flowness-oss open-alpha-demo` |
| Read-only demo verification | `flowness-oss open-alpha-demo-inspect` |
| Narrow Ledger walkthrough | `flowness-ledger-demo` |

Install v1 in a fresh environment and rebuild automation against the published
`--help` output. There is no compatibility promise for v0 shell commands,
Python imports, hooks, internal file paths, or generated artifacts.

### Configuration and state

Do not copy a v0 configuration directory or event/state store into v1. Start
from the v1 schemas and examples, explicitly review authority and external
effects, and migrate only data for which a release-specific converter and
verification procedure exists. Open Alpha does not provide a general automatic
state migration.

Credentials, private Transcripts, account/quota routing, customer data, server
configuration, and private runtime ledgers are not public migration inputs.

### Acceptance semantics

Flowness v1 separates production, evidence, independent judgment, targeted
rework, and owner release authority. A successful command or one passing judge
is not an accepted outcome when another mandatory check fails or remains a
critical Unknown.

## Existing issues and pull requests

Historical discussions retain their authors and context. Each open item is
classified as exactly one of:

- `fixed-in-flowness-v1`
- `migration-required`
- `legacy-wont-fix`
- `still-relevant`

Do not silently rewrite a v0 report as proof about v1. A `still-relevant` item
must be reproduced against a named v1 candidate before it becomes a v1 defect.

## Recommended migration path

1. Pin the legacy ref you currently use.
2. Install Flowness in a separate CPython 3.12 environment.
3. Run the [10-minute demo](oss/flowness-oss-harness/docs/open-alpha-demo.md).
4. Replace integrations one public CLI or schema at a time.
5. Recreate state from approved v1 inputs; do not share writable v0 stores.
6. Record unsupported behavior as `migration-required` or `still-relevant`
   with a minimal reproduction.

The independently accepted Alpha coordinate is Linux aarch64 with CPython
3.12. Broader compatibility remains a Beta evidence target.
