<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flowness OSS Harness

This package contains the public assurance, replay, release-audit, and
deterministic acceptance tooling shipped with Flowness Open Alpha. Its package
version follows the sealed component track (`v1.0.0-alpha` line); the current
repository release is versioned separately — see
[`docs/VERSIONING.md`](../../docs/VERSIONING.md).

Within the Flow-centered runtime, this layer protects one load-bearing
boundary: a candidate is produced by isolated producers, sealed to its content,
judged independently, repaired through targeted rework, and re-judged fresh —
a traceable accepted outcome. A mandatory `FAIL` or critical `UNKNOWN` blocks the candidate; it
cannot be cleared by an average score or a rewritten explanation.

## Run the acceptance loop

The canonical public demonstration is deterministic so evaluators can inspect
the orchestration and acceptance semantics without treating model variance as
proof. Run the following commands from a Flowness **Git checkout**, not from the
immutable root of a bare sealed export:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e './oss/flowness-oss-harness[test]'
.venv/bin/flowness-oss open-alpha-demo --output /tmp/flowness-open-alpha-demo
.venv/bin/flowness-oss open-alpha-demo-inspect --run-root /tmp/flowness-open-alpha-demo
```

For a bare export, use the repository-independent sealed-export verifier and
the clean-room acceptance entry. If you want to run this demo from exported
sources, copy the package to scratch space first; never create `.venv`, test
output, or bytecode inside the sealed directory.

The inspector prints a JSON object containing these key fields:

```json
{
  "state": "verified",
  "producer_agents": 3,
  "judge_agents_per_round": 2,
  "round_1": "blocked",
  "targeted_rework": "verified",
  "round_2": "accepted"
}
```

The demo runs three isolated producer fixtures, seals their artifacts, gives
the same candidate and policy to two judge fixtures, preserves the first
round's blocker, produces bounded successor evidence, and performs a fresh
retest. Optional Codex mode replaces only the producers; the judges remain
deterministic policy probes. This is not a model-quality benchmark.

## Open Alpha surface

- Producer and judge role registries with isolated working and output paths.
- Content-bound candidates, evidence, policies, jury verdicts, blockers,
  successor attempts, rework, retest, and read-only inspection.
- Deterministic `G0`–`G5` gates that fail closed on mandatory failures and hard
  unknowns.
- Mechanism, Claim, Unknown, Drift, Content Graph, architecture, packaging, and
  channel-staging contracts.
- A selected canonical engine package and a narrower Ledger Core package.

The first verified Alpha coordinate is **Linux aarch64 with CPython 3.12**.
The repository release `v1.1.0-alpha.1` binds a maintainer-run clean-clone
receipt and demo-inspector output for its exact source commit (see the release
assets: `CLEAN_CLONE_RECEIPT.json`, `DEMO_INSPECTOR_OUTPUT.json`,
`RELEASE_MANIFEST.json`). What remains a pre-release boundary is **independent,
non-author external acceptance** of the sealed component bytes — that gate is
still open, and no external-acceptance claim is made until it is bound.

## Maturity and boundaries

| Layer | Alpha status | Claim boundary |
| --- | --- | --- |
| [Ledger Core](../../public-core/flowness-ledger-core/README.md) | Runnable Open Alpha slice | Local decision, projection, and recovery behavior; no distributed or production claim |
| Controller, jury, rework, and canonical engine | Experimental Open Alpha | Inspectable code, packages, and tests; external clean-room and jury acceptance remain pre-release requirements; APIs may change |
| Mechanism, Claim, Drift, Content Graph, and D0–D9 Atlas | Experimental explanatory and validation machinery | Documents and diagrams do not promote target behavior to implemented behavior |
| Fleet, accounts, quotas, credentials, private context, and server operations | Excluded | No public availability or runtime claim |

Open Alpha does not establish production reliability, scale, security
hardening, benchmark leadership, compatibility across platforms, or external
adoption. Do not place unreviewed credentials or irreversible production
authority behind an Alpha worker.

## Evidence and architecture

- [10-minute demo](docs/open-alpha-demo.md)
- [D0–D9 Architecture Atlas](docs/architecture-atlas.md)
- [Gate rules](docs/gate-rules.md)
- [Mechanism excavation map](docs/mechanism-excavation-seed-map.md)
- [Drift Atlas](docs/drift-atlas-seed-v0.md)
- [Content Graph](docs/content-graph.md)
- [Open Alpha package boundary](docs/open-alpha-package-scope-v0.md)
- [Selector packet](docs/open-alpha-selector-packet-v0.md)

## From Wow-Harness

Flowness is a ground-up major-version evolution of Wow-Harness. The migration
preserves v0 history and contributor attribution while replacing the current
tree with a reviewable v1 source line. Historical stars, forks, issues, or use
belong to that lineage; they do not validate the rewritten Flowness v1.

## Publication boundary

The repository owner controls release and external channel actions. Exact
source, export, wheel, clean-room, and jury identities must be recorded in a
future immutable freeze/release record; they are neither embedded nor asserted
as completed by this mutable candidate.
Repository transfer, rename, redirect checks, and GitHub private vulnerability
reporting are release-sequence operations rather than runtime capabilities.

The owner supplied an unverified hypothesis that a DeepSeek-related discovery
opportunity may exist. This project does not claim DeepSeek endorsement,
selection, criteria, or a deadline without a verified primary source.

Code is Apache-2.0. Public documentation and media are CC-BY-4.0 unless a more
specific file-level notice applies.
