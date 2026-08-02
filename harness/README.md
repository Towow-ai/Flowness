<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Flowness Harness kernel

This is the installable canonical engine package in Flowness Open Alpha. It
contains the selected `towow` EventLog, envelope, commit-gate, projection,
orchestration, review, closure, lock, worktree, and recovery mechanisms.

Run this example from a Git checkout or a scratch copy outside any immutable
sealed-export directory:

```bash
python3.12 -m venv .venv
.venv/bin/pip install ./harness
.venv/bin/flowness-harness --json
```

The intended first independently accepted Alpha coordinate is **Linux aarch64
with CPython 3.12**. This source tree cannot self-authorize that claim: it is
valid only when an external immutable release record binds the exact commit,
sealed export, independent clean-room receipt, selected tests, canonical E2E,
and fresh-jury decision. Other systems, architectures, Python versions, and
production deployments remain unverified.

The public source is a deliberately selected subset of the full development
system. Account and quota routing, Transcript-backed supervision, live agent
spawning, credentials, fleet/server control, customer data, and private
configuration are excluded. Optional real-agent operations fail closed unless
an explicitly authorized adapter is installed.

Open Alpha means the selected source and tests can be inspected, installed,
and run within the stated coordinate. It does not mean every designed
mechanism is implemented, every branch is runtime-reachable, or the package is
production-ready. Interfaces may change before Beta.

Code is Apache-2.0. Documentation is CC-BY-4.0 unless a more specific
file-level notice applies. See `THIRD_PARTY.md` and `sbom.cdx.json` for the
Alpha dependency record.
