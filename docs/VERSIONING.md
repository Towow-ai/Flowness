# Versioning: four independent tracks

Flowness deliberately versions four things separately. Do not infer one from another.

| Track | Current | What it versions | Where it lives |
|---|---|---|---|
| Repository release | `v1.1.0-alpha.1` | The public identity, docs, and release evidence as a whole | Git tags / GitHub Releases |
| Component packages | `1.0.0a1` | The installable Python packages (`flowness-harness`, `flowness-oss-harness`, `flowness-ledger-core`) and their sealed supply chain (SBOM, locks, export audit) | `pyproject.toml` of each package |
| Event schemas | per-schema | Canonical event payload compatibility | `harness/src/towow/schemas/` |
| Demo fixtures | per-fixture | Deterministic demo/fixture reproducibility | fixture manifests in release evidence |

A repository release may ship without bumping package versions. Package
versions move when a sealed component distribution is republished or its
runtime/API contract changes; repository-side metadata edits (for example a
`pyproject` description) may remain pending until the next component reseal,
with the supply-chain manifest re-pinned to the exact current file bytes. Release evidence binds the exact commit plus the
exact package versions it shipped with.
