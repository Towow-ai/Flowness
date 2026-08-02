# Third-party dependency map — Open Alpha candidate

This mapping is generated from the two exact `uv.lock` files carried by the
candidate and `build-system-requirements.lock`, which separately pins the PEP
517 backend. The corresponding CycloneDX document is `sbom.cdx.json`; its
metadata hash-binds all three source locks.

The canonical kernel directly depends on Pydantic, Click, and PyYAML. The OSS
orchestration package directly depends on cryptography and jsonschema. Their
locked transitive closure is recorded in the SBOM, including platform-marked
dependencies.

No third-party package source is intentionally vendored. License expressions
in the SBOM are observations from installed distribution metadata, not legal
advice. `NOASSERTION` is retained where an automated observation was not
sufficient. Owner/source rights and publication authorization remain separate
gates and are false in RC0.

Remaining supply-chain boundary: the locks contain cross-platform artifact
hashes, but the repository does not yet carry a sealed multi-platform offline
wheelhouse. An offline install is therefore reproducible only where the exact
locked artifacts are already present in the supplied cache.
