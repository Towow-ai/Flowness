# Runtime Evidence Seal Protocol v0

Status: **private contract, not collected and not verified.** This protocol
turns the existing Evidence Seal job card into three independently hash-bound
records. It contains neither a server connector nor a raw runtime artifact.
It does not authorize a server read, collection, promotion, export, release or
publication.

## The minimum chain it requires

```text
frozen request (field allowlist + cutoff + exclusions)
  -> collector receipt (only object/derivative hashes and capture states)
  -> independent verifier report
  -> separate Mechanism/Claim registry review
```

The required captured chain is deliberately narrow:

```text
source identity
  + producer -> event/sentinel -> projection/watermark -> consumer
  + one real failure/recovery -> independent postcondition
```

All chain observations must carry the same seal-local `subject_link`: a
non-reversible, non-reusable pseudonym (`slr_…`) that is valid only for this
seal. The private vault handles its derivation; neither raw subject nor its
derivation is included. A real collection may not join different subjects
merely to complete a happy-looking chain.

## 1. Frozen request

`runtime-evidence-seal-request/v1` is valid only when it names a distinct
`seal_id` and one `trace_id`, exact mechanism IDs, a non-resolvable
`vault_locator_fence` (a hash reference, never a path), a closed record-sequence
range, and a closed UTC cutoff window. The validator rejects a reversed,
zero-width, malformed, or longer-than-24-hour window before collection. It also
requires one expected deployed build/commit/tree identity digest, seven exact
object classes and the exact field set for each class. It rejects omitted,
wildcard or extra fields.

| Object class | Allowed fields only |
| --- | --- |
| source identity | build/commit/tree hashes; clean state |
| producer observation | seal-local subject token; producer kind; time bucket; source reference |
| event segment | event type; sequence; sentinel relation; transition code |
| projection state | projection name; watermark; state/meta hashes |
| consumer observation | consumer kind; seal-local token; watermark; decision code |
| failure/recovery | category; fingerprint hash; recovery code; before/after hashes |
| independent postcondition | postcondition code; observation kind; state hash |

The immutable request must exclude, at minimum: credentials/secrets, transcript
or prompt/response material, customer content and identifiers, hosts/network
coordinates/private paths, environment/config values, and undeclared objects or
fields. If the collector cannot name an object and its allowed fields in advance,
the correct result is `source_unavailable`, not a broader search.

## 2. Collector receipt

The trusted collector writes its original allowlisted objects only to the
private audit vault. The protocol consumes a receipt, not those objects. The
receipt repeats and verifies the sealed trace, fence hash, record range and
cutoff; binds the one seal-local subject link to every chain observation; and
binds the expected source-identity digest to the captured source-identity audit
hash. Each chain observation must also repeat the exact audit/derived hashes of
its captured object. It contains no locator, raw payload, value, command line,
hostname, path, account, transcript or configuration.

`captured_ready_for_independent_verification` requires every requested object
and every chain role to be captured with no gaps. `not_collected`,
`rejected_before_read`, `incoherent`, `absent`, `redacted`, or `incoherent`
objects remain evidence of a gap. They cannot be turned into a success by a
summary sentence.

## 3. Independent verifier report

The verifier identity must differ from the collector identity. It recomputes
the request/receipt bindings and explicitly records eight results: scope freeze,
source identity, event coherence, projection coherence, consumer link,
failure/recovery, redaction derivation and independent postcondition. A ready
report must attach a recomputable binding from every check to the precise
captured object IDs and their audit/derived hashes, plus hashes of the complete
receipt object set and scope binding. It still does not read the vault. A
`contract_only` review may record only `unknown` or `fail` checks and can never
be ready for registry review.

A `scope_limited_evidence_ready_for_registry_review` result means only that the
receipt contract is coherent enough to be reviewed. It has this fixed effect:

```text
scope-limited runtime evidence group only
→ no mechanism status change
→ no public capability claim
→ no release eligibility change
```

Promotion remains a later review against the existing mechanism cards: it must
compare the evidence group with the card's static code/test group, declared
failure/recovery boundary and open Drift/Unknown records. A pass never makes an
uncaptured mechanism, daemon, fleet, performance property or customer outcome
current-verified.

## 4. Mapping to Mechanism and Claim registries

Each frozen request maps every selected `mechanism_id` to the only permitted
effect: `runtime_evidence_group_only_registry_review_required`. A reviewer must
then add a new evidence group with the request/receipt/report hashes and either:

- retain `experimental` / `unknown` with an explicit gap or contradiction;
- record a narrow, separately reviewed `current_verified` claim only if the
  existing registry's full promotion rule is met; or
- open/update a Drift record if deployment identity, sequence, watermark,
  consumer or recovery evidence conflicts with the static card.

This mapping deliberately cannot change a Claim or Mechanism Card by itself.

## 5. Local implementation and test boundary

`flowness_oss_harness.runtime_evidence_seal_protocol` validates the three
schemas and their hashes in memory. Its tests use fabricated SHA-256 strings;
they prove contract rejection behavior, not source existence, server access,
redaction correctness, runtime operation or independent human review. No
request, receipt or verifier report for a real server has been committed here.
