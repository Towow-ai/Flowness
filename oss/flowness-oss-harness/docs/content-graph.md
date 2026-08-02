# Content Graph

All channel material is derived from an accepted content graph. Channels do not
invent their own facts.

## Node types

- `Claim`: scoped statement with baseline, success criteria, limitations, and
  verification date.
- `Mechanism`: accepted current, experimental, target, negative, or unknown
  mechanism record.
- `Evidence`: immutable source, test, trace, benchmark, or approved external
  reference.
- `Module`: L0-L7 dependency and maturity record.
- `Diagram`: D0-D9 source and rendering.
- `Case`: input, baseline, execution, authoritative outcome, failure, recovery,
  and limits.
- `Benchmark`: versioned dataset, runner, trials, metrics, and raw result.
- `Demo`: replayable scenario bound to a candidate snapshot.
- `Section`: reusable explanation unit.
- `Asset`: README section, article, page, deck, video segment, image, FAQ, or
  call to action.
- `ChannelPackage`: ordered set of assets for a named channel and release stage.

## Allowed edges

`evidence supports|disproves|limits claim`; `mechanism implements claim`;
`module depends_on module`; `diagram depicts mechanism`; `case exercises
mechanism`; `benchmark tests claim`; `demo replays case`; `section explains
claim|mechanism`; `asset derived_from section`; `channel_package contains
asset`; `asset localized_as asset`; `node supersedes node`.

Every derivation edge stores source IDs, candidate snapshot, transform owner,
content hash, and review state. A channel package is invalid if it contains a
claim with no accepted support edge or if its scope is broader than the source
claim.

## State model

`draft → evidence_bound → jury_accepted → staged → owner_approved → published`

Any state may move to `withdrawn`; a later version may mark it `superseded`.
Only `owner_approved` can move to `published`. If supporting evidence expires,
is contradicted, or belongs to a different snapshot, downstream nodes return to
`draft` or `evidence_bound`.

## Minimum content spine

One canonical spine feeds all channels:

1. concrete failure and audience;
2. fit/no-fit and disclosed baseline;
3. three or fewer evidence-bound contributions;
4. D0/D1 overview;
5. mechanism and architecture depth via D2-D9;
6. worked success and failure cases;
7. reproducible evaluation and limitations;
8. install, operate, recover, upgrade, and contribute;
9. source and license boundary; and
10. stage-appropriate call to action.

Translations are child assets with parity review. Updating an English or
Chinese claim invalidates the paired translation until reviewed.

## Executable impact rule

`src/flowness_oss_harness/content_graph.py` supplies the small executable core
of this policy. It rejects promotable claims without an incoming evidence edge
and computes the downstream review set from a changed evidence or mechanism
node. Returned nodes must return to `evidence_bound`; the helper does not
publish, unpublish or rewrite material on its own.

The current public successor covers impact propagation plus a narrow,
hash-bound Open Alpha launch set; it is not a complete content CMS and does not
prove that external channels stay synchronized. The authoritative public
launch graph for this release line is
`registries/content-graph-open-alpha-launch-v0.json`. Exact release identities,
non-author clean-room evidence, fresh jury decisions, and owner authorization
remain required external bindings; this mutable graph does not assert that a
release record already exists.
## V2 local candidate binding

The graph is an invalidation mechanism, not a publishing mechanism. In
`content-graph/v2`, an `asset` may bind a repository-relative `source_path`
and the SHA-256 of its exact bytes. `verify_file_backed_assets` rejects an
edited README or FAQ until its graph node is deliberately refreshed; callers
can then use `affected_artifacts` to create bounded review work for every
dependent claim, section, diagram, asset and channel package.

`content-graph-completeness-ledger-candidate-v0.json` makes the current scope
executable rather than narrating a brittle file count here. It discovers every
Markdown candidate material under its named Ledger-doc and staging-brief
directories, then adds the explicit README, receipt tool, mechanism card,
Architecture Atlas appendix, and this explanation. The verifier requires that
the graph's file-backed assets equal that derived set exactly. A new Markdown
material in a scoped directory therefore fails closed until it is intentionally
hash-bound and given dependencies.

The same manifest maps every one of the fifteen seed mechanisms to a graph
node. The Architecture Atlas appendix is downstream of each mapping, so a
mechanism change returns that explanation for review. This is **not** fifteen
public product claims: the manifest also asserts that the seed registry has no
public claims for any of them. They remain local, unsealed static candidates
with the registry's Unknown and Drift boundaries intact.

The narrower Ledger D0–D2 and D3–D5 views are dependencies of the local
commit-visible and projection candidates. The local measurement summary is a
dependency of raw receipt evidence and its explicitly non-benchmark claim.
These bindings establish review work after a change; they do not establish
English/Chinese semantic parity, external publication, runtime truth, rights,
or release authorization.

## Legacy V3 private relationship migration (excluded from Open Alpha)

The historical `registries/content-graph-ledger-candidate-v3.json` was an
explicit private migration overlay over the named V2 candidate graph. It is
excluded from the Open Alpha export and is not the current public graph. The
public successor is
`registries/content-graph-open-alpha-launch-v0.json`, generated from the
evidence-bound launch registry and verified against current included assets.

For historical context, the V3 overlay bound the exact V2 file hash and added
first-class `limitation`, `audience_profile`, `channel`, and `version` records.
Every V2 candidate asset was accounted for exactly once with its audience,
private-workspace channel, locale, draft capability, candidate version,
source-node IDs and hashes, transform record, local review record, claim scope,
and retained limitations. A relationship could use the literal
`NotApplicable`, but it could not silently omit a field.

`content_graph_v3.py` fails closed if a source hash, candidate snapshot,
transform producer, claim scope, limitation set, audience/channel/locale, or
version link drifts.  It also extends the impact query: changing a V3
limitation returns the claims and assets that retained it for review.  The
current EN/ZH FAQ pair is represented as **unreviewed**; a bilingual draft
package using it is therefore rejected until real parity review exists.  This
is an explicit blocker, not a synthetic parity result.

The only registered channel is `channel.private-workspace`, whose capability
is `draft`.  `validate_channel_package_v3` can validate a prospective draft
package's candidate identity, exact source hashes, retained limitations, and
parity condition.  It has no send, scheduling, publishing, analytics, or
owner-approval path.  A future sealed export and separate owner-gated channel
package contract are still required before any external distribution.

## Immutable review plans

`content_impact_review_plan.py` compares a predecessor and current compatible
V3 graph (or two same-snapshot V2 migration inputs) and derives changes from
the graph bytes; callers cannot label arbitrary nodes as changed.  It seals a
local-only plan with both graph identities/hashes, changed source/evidence/
mechanism/asset records, ripple, review obligations and invalidated draft
channel-package slots.  A V2/V3 pair or any candidate/snapshot/version
mismatch is rejected rather than guessed across a migration boundary.

The plan is deliberately a review worklist, not a job queue.  It has no
publisher, scheduler, owner approval, send, analytics or source-rewrite path.
Changing a README node, evidence node, static-chain mechanism node, limitation
or an asset relationship can make downstream material require review, but it
does not change its state or place anything in a public channel.
