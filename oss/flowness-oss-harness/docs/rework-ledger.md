<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Meta-review rework ledger

This ledger preserves the original blocker identities from the independent
meta review. A configuration or documentation edit moves a blocker to
`reworked_pending_retest`; it does not close the blocker. Closure requires the
listed retest against the canonical consumer.

Status: **historical-to-current Open Alpha rework record**. Several entries
were written during private staging and intentionally retain their original
failure coordinates. Those coordinates explain why the experimental Open
Alpha contracts exist; they do not describe the current repository as private,
unlicensed, production-verified, or release-authorized.

## R2-CONTENT-BINDING-CHAIN-002 — README rebind stopped at the first graph layer

**Original failure:** the Ledger README gained the bounded demo-scenario pack,
but its exact hash remained stale in `content-graph/v2`.  Because V3, six
immutable manual channel packages and the EN/ZH parity contract were treated
as separate artifacts, a superficial repair could update only the V2 asset
hash and leave downstream byte bindings stale or absent.

**Rework:** the README hash is rebound in V2, V3 now names the resulting exact
V2 bytes, and every checked-in channel package names the resulting exact V3
bytes and receives a new immutable `package_id`/`package_hash`.  The parity
contract now binds the exact V3 bytes too; its verifier rejects a changed graph
at the same path.  A regression scenario mutates a copied README and proves
that V2-only repair fails V3, V3-only repair fails parity and every package,
and only the full V2 → V3 → parity/package rebind restores bounded validation.

**Boundary:** this is content provenance maintenance only.  It does not review
the new demo wording, approve translation semantics, authorize any channel,
or alter the historical local-unsealed limitations. Current Open Alpha claims
remain experimental and retain explicit no-production, bounded-clean-room,
and owner-controlled-publication limits.

**Status:** `reworked_pending_retest`.

**Retest condition:** an independent reviewer changes a file-backed candidate
asset in a successor tree and verifies the same three failure stages, including
every channel package and parity contract.  The reviewer must then inspect the
rebound prose and limitations independently; byte-consistency is not semantic
or publication approval.

## R2-CLARITY-DOSSIER-INTEGRITY-001 — owner decision dossier source binding was stale

**Original failure:** the hash-bound owner dossier referenced an older
`public_package_preflight.py` byte sequence. Its deliberate fail-closed check
therefore prevented every normal route comparison, deterministic render and
writer test from reaching their intended assertion.

**Rework:** the canonical dossier source and its checked-in Markdown render now
bind the actual current preflight bytes
`sha256:601ba57915bb8bd0b3a91226ab548ebc68c991265d5de0f3973cada790045d40`.
All seven declared sources were re-hashed against this checkout: six were
unchanged and only `public_package_preflight` required a rebind. The adversarial
test now mutates each declared source ID in turn, so any later source drift
must fail with that source's own mismatch code. The Markdown remains accepted
only when it is the deterministic rendering of the verified matrix.

**Boundary:** at the historical review coordinate, rebinding local bytes
neither changed any route from `not_ready` nor cleared rights, runtime,
clean-room, migration, jury, or owner-authorization gates. The dossier remains
an experimental comparison aid and cannot itself authorize an export,
repository action, release, or channel publication.

**Status:** `reworked_pending_retest`.

**Retest condition:** from the successor candidate, run the full
`test_owner_decision_dossier.py` suite. Confirm normal comparison, hidden
delta, deterministic-render, unknown-reference and one-time-writer paths all
run; then mutate each one of the seven declared source hashes and confirm the
specific `OWNER-DOSSIER-SOURCE-HASH-MISMATCH:<source_id>` rejection. Independently
check that every route still retains its required rights, runtime, clean-room,
migration, jury and explicit owner-action gate.

## JURY-CLAIM-CONTENT-GRAPH-001 — architecture and measurement assets bypassed invalidation

**Original failure:** the file-backed Content Graph covered selected Ledger
materials, while D0–D2, D3–D5 and the local measurement summary could carry
candidate wording without entering a graph-derived review set.

**Rework:** the `content-graph-completeness/v1` manifest was introduced against
the then-private candidate Markdown directories and combines the current
allowlisted materials with an explicit list for singular assets.
`verify_content_graph_completeness_files`
rejects any mismatch between that derived source-path set and hash-bound graph
assets. D0–D2, D3–D5, the measurement summary, the Architecture Atlas appendix
and the graph explanation are now exact-byte assets. Raw local measurement
receipt evidence invalidates the measurement summary; the two Ledger
architecture documents depend on their local mechanism nodes; every seed
mechanism invalidates the Atlas appendix.

**Boundary:** this closes neither runtime nor rights evidence. The change only
creates local review work; it does not prove that an asset is public-ready,
semantically correct after review, or eligible for publication.

**Status:** `reworked_pending_retest`.

**Retest condition:** an independent reviewer verifies the manifest from a
sealed candidate, adds one scoped candidate-facing Markdown file without an
asset entry and observes a fail-closed completeness mismatch, then changes a
seed mechanism and the raw-measurement evidence respectively and confirms the
Atlas/D0–D5/measurement review sets are returned. The reviewer must verify all
asset byte hashes before any later jury status changes.

## JURY-CLAIM-CONTENT-GRAPH-002 — graph documentation understated coverage

**Original failure:** the Content Graph explanation claimed a much smaller
README/FAQ scope than the registry actually verified, so a reviewer could not
derive the active candidate-facing surface from a single truthful source.

**Rework:** `docs/content-graph.md` is itself a hash-bound asset and describes
the executable completeness manifest rather than recording a stale number or
hand-maintained file list. The verifier returns the sorted derived source set
and count; documentation, architecture and jury review should consume that
result instead of reciting a separate scope.

**Boundary:** it remains an experimental Open Alpha governance explanation,
not a content-management service, translation-parity proof, runtime assertion,
or publication authorization.

**Status:** `reworked_pending_retest`.

**Retest condition:** independently compare the manifest result to every graph
asset at a sealed candidate and alter either set in a fixture; both a missing
and an extra entry must fail. Verify that a change to this explanation itself
causes its file hash check to fail until deliberately rebound.

## BLK-MECH-CLAIM-TRACE-004 — full-system mechanism narrative lacked graph traceability

**Original failure:** the fifteen seed mechanisms had no public claims and
only the narrow Ledger mechanism appeared in the Content Graph, leaving the
Architecture Atlas outside mechanism-change impact analysis.

**Rework:** the completeness manifest maps every seed ID to an existing graph
mechanism node and requires an exact keyset match with the seed registry. The
Architecture Atlas appendix depends on all fifteen nodes. The same manifest
also declares all fifteen as `unrepresented_public_claim_seed_ids` and rejects
the setup if the seed registry gains a public claim without an explicit later
rework. This keeps the useful mechanism-to-asset trace while refusing to turn
local static studies into external product claims.

**Boundary:** these began as local unsealed static mappings and are now exposed
only as experimental Open Alpha mappings. They do not replace the registry's
Unknown/Drift records or establish a deployed producer, consumer, production
runtime, or stronger public claim.

**Status:** `reworked_pending_retest`.

**Retest condition:** for every seed mechanism, an independent reviewer
changes the node in a fixture and verifies that the Atlas appendix is in the
computed impact set; remove one mapping and verify failure. If a future public
claim is added to the seed registry, it must receive its own evidence, claim,
asset and jury review rather than bypassing this explicit Unknown boundary.

## BLK-MECH-COVERAGE-INVENTORY-001 — seed 完整性没有可验证母集

**Original failure:** the static-chain catalog could only enumerate the 15
preselected seed IDs, so a new private entry point, durable state, event,
projection, daemon/watcher/worker/hook, state path, irreversible action,
public surface, or recovery path could sit outside that list without causing
the catalog to fail.

**Rework:** `coverage_inventory.py` rebuilds a scoped local static universe
from the historical development Harness tree and the Ledger Candidate source
tree. The public Open Alpha exports only the explicitly allowlisted subset.
Its declared object classes are fixed in
`registries/coverage-inventory-scope-v0.json`. Each discovered object receives
a stable inventory ID, source locator, producer/consumer relation IDs when
static discovery found them, and exactly one highest-priority mapping to seed
mechanism IDs or a generated explicit `UNKNOWN-COVERAGE-INVENTORY-*` record.
Missing mappings, tied rules, unknown mechanism IDs, source-scope changes, and
any edited generated inventory all fail closed. The broad private fallback is
intentional: it exposes an unclassified object as an explicit Unknown rather
than silently pretending it belongs to one of the 15 seed mechanisms.

**Boundary:** this is local, unsealed static discovery only. It does not cover
source outside the two declared historical scopes, runtime reachability, deployed
daemon/hook state, authority, rights or release readiness. A mapped seed is
not promoted beyond the existing `candidate_mapped_only` ceiling.

**Status:** `reworked_pending_retest`.

**Retest condition:** independently run the current-coverage test and rebuild
an inventory from the same checkout. Randomly sample every declared class and
trace it to either a seed mechanism or explicit Unknown. Remove one mapping
from the rebuilt object list and verify `verify_coverage_inventory` rejects
it; add a discovered fixture with no matching assignment rule and verify the
builder rejects it. A future source scope change must be reviewed by an
independent Coverage Judge rather than accepted as proof of complete server
coverage.

## BLK-MECH-CROSS-LAYER-002 — key architecture arrows lacked an interface contract

**Original failure:** the D1/D2/D5 architecture views connected individually
mapped mechanisms, but no machine-readable record said which state, authority,
provenance, failure owner, or recovery owner crossed each non-decorative arrow.

**Rework:** `architecture-cross-layer-edges-local-v0.json` now binds all 47
semantic D1/D2/D5 arrows to stable edge IDs.  Every entry records both
mechanism/plane endpoints, producer output, consumer input, authoritative
state, schema version or explicit Unknown, authority, correlation/provenance,
failure/recovery ownership, evidence locator, and `candidate_static` or
`unknown` boundary.  The verifier reads the diagram markers and Mermaid arrows
and fails on an omission, endpoint drift, or an Unknown endpoint presented as
static evidence.

**Boundary:** this makes the local architecture's stated handoffs inspectable;
it does not create an end-to-end runtime trace, prove worker/session identity,
or establish a deployed authorization or recovery path.

**Status:** `reworked_pending_retest`.

**Retest condition:** from a successor sealed candidate, remove one diagram
marker, alter one rendered endpoint, and upgrade an Unknown worker edge to
`candidate_static`; all three must fail.  Then trace one D1, D2, and D5 edge
to their static evidence while retaining the runtime Unknown for each.

## BLK-MECH-HISTORY-LINK-003 — history anchors were not bound to current nodes

**Original failure:** a history anchor previously proved only that a local
commit changed a declared path.  It did not show whether the historical change
still related to a current, hash-checked static node.

**Rework:** the v2 history registry binds all 17 retained anchors to a static
manifest/node reference, current path/excerpt hash/symbol, a relation kind, and
an exact per-path historical patch hash/symbol.  Direct `introduced` and
`changed` relations require current path/symbol continuity; non-equivalent
older implementations are explicitly `superseded`.  Verification rechecks the
static chain and rejects a removed/replaced node, current-hash drift, patch
mismatch, or an unrelated same-path hunk.

**Boundary:** this is a local Git-evolution link, not semantic equivalence for
`superseded` code, runtime execution evidence, export rights, or a release
claim.

**Status:** `reworked_pending_retest`.

**Retest condition:** on a successor sealed candidate, independently verify
every anchor; use fixtures for a replaced static node, hash drift, and an
unrelated symbol on the same path, all of which must fail.  Retain every
`superseded` relation as non-current evidence rather than promoting it.

## OSS-META-P0-2 — jury outputs were disconnected from release evaluation

**Original failure:** all 26 jury roles emitted
`judge-verdict.schema.json`, while `release-evaluate` consumes
`jury-report.schema.json`. Candidate sealing also produced an artifact
manifest without defining the inputs needed to assemble the richer release
candidate.

**Rework:** jury roles now declare `jury-report.schema.json` directly and name
their assigned checks. The private candidate-assembler contract remains
withheld; the public, inspectable successor boundary is the
[Open Alpha package scope](open-alpha-package-scope-v0.md). The sealed-export
implementation fails closed on scope, rights, dependency, and public-consumer
closure.

**Status:** `reworked_pending_retest`.

**Retest condition:** in a clean workspace, run
`run-create -> producer waves -> candidate-seal -> candidate-assemble ->
jury-run-create -> jury wave -> release-evaluate`; verify that all 26 role
outputs validate as jury reports and are consumed without format conversion or
manually fabricated reports. The candidate assembler must reject missing,
changed, dirty, cross-snapshot, duplicate and dangling inputs.

## OSS-META-P0-3 — Schema and engine semantics drifted

**Original failure:** the evaluator accepted records that violated the input
Schemas, while its decision output contained `check.role_coverage` and
`blocked_invalid_fail`, neither accepted by the release-decision Schema.

**Rework:** the policy Schema now expresses per-check dimension and role
authority; the engine reports role coverage once at gate level, emits a
non-empty judge state for uncovered checks, and represents an untrusted Fail as
Pending plus a system blocker. The release-decision Schema uses those same
fields and enums. The strict candidate and jury input contracts remain
fail-closed rather than being weakened to match malformed fixtures. Owner
authorization has a separate Ed25519-signed record Schema and a separate
root-owned trusted-key registry Schema.

**Status:** `reworked_pending_retest`.

**Retest condition:** validate the default policy, a complete candidate, all
jury reports, the release decision and owner approval with Draft 2020-12
format checking. Then submit one malformed example for every required field,
enum and cross-record binding and verify rejection before evaluation. Validate
the engine's blocked, pending-coverage, trusted-fail, untrusted-fail and pass
outputs against `release-decision.schema.json`.

## OSS-META-P1-6 — gate-wide roles were required on every check

**Original failure:** each gate listed a union of roles and the evaluator
treated every role in that union as mandatory on every check. Correct
dimension-specific reports were therefore marked missing.

**Rework:** every policy check now declares one dimension and exactly two
required/allowed roles. Every judge role declares the checks it may perform.
The gate-level role list is only the union used for gate planning; it does not
grant or require check authority.

**Status:** `reworked_pending_retest`.

**Retest condition:** for every policy check, assert that required roles equal
allowed roles, contain exactly two distinct judges, match the role's dimension,
and appear in that role's declared checks. Run G3 with each pair reporting
only its assigned checks; all six checks must receive two reports without
requiring unrelated G3 roles. An unauthorized role/check pair must be rejected
as a system blocker.

## OSS-META-P1-7 — quick-start commands depended on an undeclared global CLI

**Original failure:** the instructions installed into `.venv` and then invoked
bare `flowness-oss` without activating the environment.

**Rework:** README and first-run commands invoke
`.venv/bin/flowness-oss` explicitly.

**Status:** `reworked_pending_retest`.

**Retest condition:** execute the documented quick start line-for-line in a
clean shell where `flowness-oss` is absent from `PATH`. Every CLI invocation
must resolve to the newly created virtual environment. Do not count a machine
with a pre-existing global installation as evidence.
