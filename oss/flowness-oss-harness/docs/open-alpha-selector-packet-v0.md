# Flowness Open Alpha — selector packet

> Open Alpha pre-release material. Independent clean-room, fresh jury, and exact release records are still required.

## 30-second fit

Flowness is a candidate for inspection because its Open Alpha is organized around an explicit goal → producers → sealed evidence → independent jury → targeted rework → acceptance loop. The default proof is a deterministic local fixture; optional Codex mode replaces only producers, while judges remain deterministic policy probes. Its launch claims and assets are hash-bound to mechanisms, evidence and Drift in a candidate Content Graph. This is an invitation to inspect the source and trace, not a claim that DeepSeek has endorsed, selected, or specified criteria for Flowness.

The runnable proof today uses deterministic producer and judge fixtures. Optional Codex mode replaces only the producers with real Codex processes; judgment remains a deterministic policy probe. This proves the local orchestration and acceptance semantics, not model quality, production reliability, scale, security, or adoption.

## Exact candidate identity

- Package version: `1.0.0a1`
- Tag candidate: `v1.0.0-alpha`
- Exact release commit: `EXTERNAL_RELEASE_RECORD`
- Exact sealed export manifest hash: `EXTERNAL_RELEASE_RECORD`
- Scope policy: `config/open-alpha-package-scope.json`
- Scope policy SHA-256: `sha256:a05b6bc18a617f819bf03fce007f5f66cf8e2a26a2667ab953188e7733f7d1e9`

`EXTERNAL_RELEASE_RECORD` is a required future binding for the exact commit, tree, export, wheel, non-author clean-room result, fresh jury decision, and authorization. This mutable packet does not assert that record already exists.

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
- Exact commit, tree, export, wheel, non-author clean-room, fresh jury, and authorization identities remain pre-release requirements; this mutable packet does not claim they already exist.

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
- Repository state and external actions must be verified in GitHub at release time, and exact acceptance identities must be retained externally; both remain pending in this packet.

## License and contact

- Code: Apache-2.0; public documentation/media: CC-BY-4.0 unless a file-level notice says otherwise.
- Questions, defects, and evidence challenges: use the current repository issue tracker; report vulnerabilities through the route in the root SECURITY.md.
- Start with `docs/open-alpha-demo.md`, then inspect `trace.json` and the Claim ledger.
