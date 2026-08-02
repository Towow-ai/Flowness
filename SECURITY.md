<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Security policy

Flowness is Open Alpha software. It has not established production security,
reliability, isolation, or scale guarantees. Do not give an Alpha worker
unreviewed credentials or irreversible production authority.

## Reporting a vulnerability

Report vulnerabilities through GitHub Private Vulnerability Reporting:

<https://github.com/Towow-ai/Flowness/security/advisories/new>

Do not disclose a suspected vulnerability in a public issue. Include the
affected version, platform, reproduction steps, impact, and any suggested
mitigation. Maintainers will acknowledge and coordinate disclosure through the
private advisory thread.

Enabling this route and validating a private report/readback is a release gate
after repository rename and before public release. If the private-report link
is not yet active, do not publish the details; wait until the release gate is
complete.

## Supported versions

| Version | Security updates |
| --- | --- |
| Latest Flowness `v1.0.0-alpha` prerelease | Best effort |
| Earlier Flowness Alpha prereleases | Upgrade to the latest Alpha |
| Wow-Harness `v0.x` | Legacy; not actively maintained |
