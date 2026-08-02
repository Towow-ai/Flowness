# Ledger candidate demo scenario pack

Status: **public Open Alpha / local evidence-derived explanation**. The
scenario pack is a public developer, professional, and general-audience demo
asset. It is not a runtime trace, benchmark, customer case, production result,
or external-adoption claim.

## Purpose

`create_demo_scenario_pack()` consumes an already verified `demo-run.json`.
It verifies the demo manifest, ledger and recovery receipt first, then creates
a new, self-hashed pack with four deterministic artifacts:

- `scenario-pack.json` — input binding and hashes for every derived artifact;
- `timeline.json` — machine-readable accepted, rejected/conflict, recovery,
  projection-staleness, and pending-verdict-refusal facts;
- `timeline.md` — the same facts in general, developer, and professional
  language with D0–D5 and mechanism-card reading links; and
- `timeline.mmd` — readable Mermaid source, not a live topology diagram.

Projection freshness and the pending-verdict negative path execute only on a
temporary byte-copy of the verified ledger. The source demo is never changed.
The pack intentionally omits the copy's random IDs and timestamps, so repeated
pack creation from the same verified input produces identical bytes.

## Local use

After the candidate demo has been created and verified, derive and then verify
the explanation pack with the installed CLI:

```sh
flowness-ledger-demo --scenario-pack-from-demo "$DEMO_DIR" \
  --scenario-pack-dir "$SCENARIO_DIR"
flowness-ledger-demo --verify-scenario-pack-from-demo "$DEMO_DIR" \
  --scenario-pack-dir "$SCENARIO_DIR"
```

Both the demo directory and scenario directory are local run artifacts.
`SCENARIO_DIR` must be a new empty directory at creation. A changed
input manifest, ledger, recovery receipt, generated timeline, Markdown, or
Mermaid source makes verification fail closed.

## Reading boundary

The pack turns one bounded local observation into an explainable timeline; it
does not extend the observation. The exact code and independent limits remain
in [D0–D2](LEDGER_CANDIDATE_ARCHITECTURE_D0_D2.md),
[D3–D5](LEDGER_CANDIDATE_ARCHITECTURE_D3_D5.md), the
[casebook](LEDGER_CANDIDATE_CASEBOOK.md), and the
[technical report](LEDGER_CANDIDATE_TECHNICAL_REPORT.md). It does not prove
multi-agent operation, real authorization, production recovery, external
adoption, performance, installability outside the verified coordinates, or
production readiness.
