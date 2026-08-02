# Flowness Open Alpha — selector packet

> Released Open Alpha material. The identity below is v1.0.0-alpha; any changed successor must carry fresh clean-room and jury evidence.

## 30-second fit

Flowness is designed around a path from a vague goal through interview, design, engineering specification, consensus, planning, execution, review, and correction. Its six-depth routing model aims to return failures to the earliest layer that must genuinely change instead of triggering another blind retry. Runtime maturity varies: the private design and engineering stages are partial, automatic tier routing and engineering-spec → consensus publishing are not closed, and the public Open Alpha currently runs only the execution → jury → targeted rework → acceptance kernel.

The runnable proof today uses deterministic producer and judge fixtures. Optional Codex mode replaces only the producers with real Codex processes; judgment remains a deterministic policy probe. This proves the local orchestration and acceptance semantics, not model quality, production reliability, scale, security, or adoption.

## Exact released identity

- Package version: `1.0.0a1`
- Released tag: `v1.0.0-alpha`
- Exact release commit: `db9cda3f82cea192c92f30ccca6ff9f12d5a1d31`
- Exact sealed export manifest hash: `sha256:06286399212db1f5f9c8cdef43cacac9afa5139122d2770632d40cc1fc3cdf42`
- Scope policy: `config/open-alpha-package-scope.json`
- Scope policy SHA-256: `sha256:a05b6bc18a617f819bf03fce007f5f66cf8e2a26a2667ab953188e7733f7d1e9`

These values identify the released v1.0.0-alpha package. They do not validate later repository changes; a successor needs a new exact commit, export identity, non-author clean-room result, fresh jury decision, and authorization.

## Run the smallest proof

From `oss/flowness-oss-harness` in a Git checkout, or from a scratch copy outside an immutable sealed export:

```bash
python -m venv .venv && .venv/bin/pip install -e .
.venv/bin/flowness-oss open-alpha-demo --output /tmp/flowness-open-alpha-demo
.venv/bin/flowness-oss open-alpha-demo-inspect --run-root /tmp/flowness-open-alpha-demo
```

Expected terminal summary: `JSON with state=verified, producer_agents=3, judge_agents_per_round=2, round_1=blocked, targeted_rework=verified, and round_2=accepted`

## Available now

- A deterministic local FAIL → targeted rework → fresh PASS demonstration with content-bound artifacts and a read-only inspector.
- Experimental source for orchestration, jury, rework, mechanism, Claim, Drift, Content Graph, and selected canonical Harness mechanisms.
- A locally exercised Ledger Core candidate with bounded tests and explicit limitations.

## Not available or not proven

- No claim of model-quality evaluation, production reliability, scale, security hardening, or external adoption.
- No public fleet, account/quota routing, Transcript-backed supervision, credentials, customer data, or server controls.
- The full interview → design → engineering-spec → consensus pipeline is not yet part of the runnable public slice.

## Evidence to inspect

- Run the 10-minute deterministic Harness demo and inspect both candidate hashes, jury reports, blocker lineage, successor evidence, and the read-only verification result.
- Read the D0–D9 Architecture Atlas from user goal through runtime, deployment boundary, authority, provenance, and target-state views.
- Trace one mechanism from registry entry to code/test evidence, current drift, public claim ceiling, consumer, failure, and recovery path.
- Confirm that a mandatory FAIL or critical UNKNOWN cannot be cleared by score averaging or narrative rewriting.
- Check that public-source scope excludes credentials, Transcripts, customer material, account/quota/fleet controls, server configuration, runtime ledgers, and rights-unknown assets.

## Maturity

- **Ledger Core — `experimental`:** Runnable Open Alpha slice for append-only decisions, visibility, projection freshness, verdict readback, and bounded crash-tail recovery; no distributed or production claim.
- **Multi-agent controller, jury, and rework — `experimental`:** Executable reference implementation and local tests; interfaces and packaging may change.
- **Mechanism, Claim, Drift, Content Graph, and architecture machinery — `experimental`:** Inspectable contracts and candidate tooling with uneven evidence depth.
- **Full private runtime and future extensions — `designed_target`:** Architecture preview is not a current public capability claim.
- **Fleet, accounts, quotas, secrets, private context, and server operations — `private_excluded`:** Outside Open Alpha and not advertised as public source.

## Selection boundaries

- The owner supplied an unverified hypothesis that a DeepSeek-related discovery opportunity may exist; no primary-source selection activity, criteria, endorsement, or deadline was verified.
- No external project in the comparison queue has been inspected in this pass.
- No star count, benchmark rank, production reliability, scale, security, or adoption claim is made.
- The repository is public at Towow-ai/Flowness; this packet makes no claim about a future selector decision or endorsement.

## License and contact

- Code: Apache-2.0; public documentation/media: CC-BY-4.0 unless a file-level notice says otherwise.
- Questions, defects, and evidence challenges: use the current repository issue tracker; report vulnerabilities through the route in the root SECURITY.md.
- Start with `docs/open-alpha-demo.md`, then inspect `trace.json` and the Claim ledger.
