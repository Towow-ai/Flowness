# Open Alpha release audit v0

This audit is evaluated against the file-exact manifest produced by
`open_alpha_package_scope.py`. It scans only records whose disposition is
`include`; excluded private material is never opened by the scanner.

## What is now present

- contribution, conduct, and security entry files;
- the active Apache-2.0 code / CC-BY-4.0 documentation license matrix;
- current NOTICE and locked third-party dependency material;
- exact-byte secret, credential, personal-path, and identity-pattern scanning;
- group coverage for every included file's rights and source/IP review state;
- immutable report hashing and a schema;
- a non-bypassable rule that this audit cannot create owner authorization.

Findings contain only rule, path, line, and a hash of the match. A secret value
must never be copied into the report. A fixture exception is accepted only when
the exact path, rule, and complete-line hash match the reviewed allowance; stale
exceptions become findings.

## Fail-closed blockers

| Blocker | Meaning | Closure evidence |
|---|---|---|
| `OA-LICENSE-001` | Exact license texts, path-level SPDX mapping, or package metadata drifted. | Restore the official texts and mappings, then seal a successor. |
| `OA-RIGHTS-001` | One or more included groups lack the owner-attested distribution-rights decision. | Bind origin and rights evidence or exclude/replace the affected bytes. |
| `OA-IP-001` | One or more included groups lack the completed source/IP disposition. | Complete source review or exclude/replace the affected bytes. |
| `OA-THIRD-PARTY-001` | The exact unified lock, SBOM, source locks, or third-party mapping drifted. | Regenerate and bind the transitive dependency materials. |
| `OA-COMMUNITY-SECURITY-CONTACT-001` | The final private vulnerability reporting route is not yet authenticated. | Enabled GitHub private reporting or an owner-approved authenticated contact. |
| `OA-SENSITIVE-001` | A high-confidence finding or stale byte-exact fixture allowance exists. | Remove/replace it, or justify an exact test-only allowance, then rerun. |
| `OA-OWNER-001` | No owner decision is bound to the exact sealed export and release identity. | Separate authenticated owner approval after every other blocker closes. |

The current policy carries owner-attested rights and IP dispositions for the
selected public groups, but the audit still rechecks exact membership and
fails closed on any new or unmapped byte. `release_ready` remains false because
owner release authorization and the final GitHub security route are external
actions. The audit does not replace legal advice, runtime evidence, clean-room
installation, jury review, repository mutation approval, or channel
publication approval.

## Alpha support coordinate

The declared Open Alpha clean-room target is CPython 3.12 on Linux aarch64,
matching the independently observed Linux aarch64 acceptance coordinate. Patch-level Python, kernel, and glibc
versions are recorded in the receipt but are not frozen by the policy. The
receipt must also bind the structured coordinate `{python: 3.12, system: Linux,
machine: aarch64}`; a matching-looking platform string alone is insufficient.

Linux x86_64 remains an explicit Beta/Unknown portability target until a real
x86_64 clean-room receipt passes the same package, E2E, isolation, and
post-verification gates. No emulated or rewritten platform value can satisfy
that missing evidence.

Run from `oss/flowness-oss-harness` after the tracked candidate roots are clean:

```bash
PYTHONPATH=src ./.venv/bin/python tools/audit_open_alpha_release.py \
  --repo ../.. \
  --scope-policy config/open-alpha-package-scope.json \
  --audit-policy config/open-alpha-release-audit.json \
  --schema schemas/open-alpha-release-audit.schema.json
```

Exit code `2` means the report was produced with one or more gates open; the
candidate audit intentionally cannot manufacture the external owner gate, so a
technical pass still retains that blocker until the immutable release record
binds the authorization. Validation failures use a nonzero exception exit and
must not be interpreted as an audit result.
