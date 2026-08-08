# Versioning: four independent tracks

Flowness deliberately versions four things separately. Do not infer one from another.

| Track | Current | What it versions | Where it lives |
|---|---|---|---|
| Repository release | `v1.1.0-alpha` | The public identity, docs, and release evidence as a whole | Git tags / GitHub Releases |
| Component packages | `1.0.0a1` | The installable Python packages (`flowness-harness`, `flowness-oss-harness`, `flowness-ledger-core`) and their sealed supply chain (SBOM, locks, export audit) | `pyproject.toml` of each package |
| Event schemas | per-schema | Canonical event payload compatibility | `harness/src/towow/schemas/` |
| Demo fixtures | per-fixture | Deterministic demo/fixture reproducibility | fixture manifests in release evidence |

A repository release may ship without bumping package versions (identity and
documentation changed; sealed packages did not). Package versions only move
when package contents change, because every bump re-seals the supply chain
(locks, SBOM, export audit). Release evidence binds the exact commit plus the
exact package versions it shipped with.
