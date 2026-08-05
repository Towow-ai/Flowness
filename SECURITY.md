# Security Policy

## Supported versions

Until stable releases are published, security fixes target the latest tagged Open Alpha and the current main branch where feasible. Each release should publish an explicit support table.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities involving:

- arbitrary code execution;
- sandbox escape;
- secret exposure;
- authority or identity forgery;
- evidence or release-manifest forgery;
- path traversal;
- unsafe deserialization;
- benchmark holdout leakage;
- permission bypass;
- production action without required human authority.

Use GitHub private vulnerability reporting when enabled. If it is unavailable, contact the maintainer address published in the repository metadata or security policy of the current release.

Include:

- affected release and source commit;
- minimal reproduction;
- impact;
- required privileges;
- whether evidence, Work state, or authority can be forged;
- proposed mitigation if known.

## Security principles

- Safety-critical decidable checks should be enforced outside model prompts.
- Model output is an untrusted claim until verified.
- Human authority cannot be synthesized by a controller.
- Evidence must bind exact objects, versions, source commits, and manifests.
- Secrets should be exposed through bounded tool interfaces, not copied into Agent context.
- Public demos must not contain private dogfood coordinates or hidden benchmark labels.
- Unknown schema versions in immutable truth should fail explicitly rather than disappear silently.
