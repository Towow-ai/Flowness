# Deterministic release gates

The release harness is conjunctive, not score based. A polished package cannot
compensate for a security, truth, install, recovery, evidence, or governance
failure. `config/governance-policy.json` is the machine policy; this document
explains its interpretation.

## Gate map

| Gate | Question | Default status |
| --- | --- | --- |
| G0 | Is the candidate legally, privately, and operationally safe to inspect and publish? | Mandatory |
| G1 | Can a new user install it and reach a verified outcome? | Mandatory |
| G2 | Do isolation, cancellation, retry, resume, and provenance survive failure? | Mandatory |
| G3 | Are mechanism coverage, claims, evaluations, baselines, negative cases, and raw trials reproducible? | Mandatory |
| G4 | Can an external adopter and maintainer operate, upgrade, roll back, secure, and govern it? | Mandatory |
| G5 | Are positioning, demos, cases, benchmark narrative, and channel assets ready? | Advisory for alpha; mandatory when the stage profile says so |

The stage profile, rather than a prose promise, decides which gates are
mandatory. Higher-level content or channel work never clears a lower gate.

## Two-judge coverage

Every required check in `config/governance-policy.json` names one dimension and
exactly two `required_roles`/`allowed_roles`. The same check IDs are declared
on those roles in `config/roles.json`. A gate-level role list is only the union
used to plan the gate; it never means that every role must evaluate every
check. First-pass judges use distinct agent instances, receive the same
immutable candidate, and may not read a peer's verdict. A producer or
candidate author cannot act as a judge.

The two reports are independent observations, not votes. Two Pass verdicts
provide coverage. One credible Fail blocks. A Pass/Fail disagreement triggers
retest and adjudication; the Pass does not cancel the Fail.

## Verdict algorithm

1. Reject reports with the wrong candidate, snapshot, policy version, role,
   identity, or first-pass attestations.
2. Compute effective module maturity from evidence types. A declared level is
   capped at the highest cumulatively supported level.
3. Resolve module dependencies. Missing modules, dependency cycles, dangling
   evidence, and unmet minimum maturity create system blockers.
4. For every required check, accept only its two allowed roles and require one
   distinct valid judge instance from each role. Verify that the report's
   dimension matches the policy check and the role declaration.
5. Treat a Fail as credible only when it has a stable blocker ID, evidence,
   observed behavior, reproduction data, remediation, and a retest condition.
6. Treat critical Unknown as a release blocker. A noncritical Unknown remains
   pending and becomes blocking at a release cutoff.
7. Accept N/A only where the policy explicitly allows it and an independent
   adjudicator approves the rationale.
8. Pass a mandatory gate only when every required check is Pass or approved
   N/A, role coverage is complete, relevant maturity dependencies pass, and no
   blocker remains open.
9. Compute the engine verdict as logical AND across all mandatory gates.
10. Authorize release only when the engine verdict is Pass and the owner
    separately signs an Approve record conforming to
    `schemas/owner-approval.schema.json`, bound to the candidate snapshot,
    policy hash and decision hash.

The evaluator resolves `owner_id` plus `key_id` only from a root-owned registry
conforming to `schemas/trusted-owner-keys.schema.json`. It rejects unknown,
revoked, not-yet-valid, expired, mismatched, or invalid Ed25519 approvals. The
approval payload never supplies a public key. `decision_hash` is computed from
the deterministic engine decision before `owner_decision`,
`release_authorized`, and `decision_hash` are added, so the signed approval can
bind that stable base without a hash cycle.

The Ed25519 message is UTF-8 canonical JSON of every owner-approval field
except `signature`, with keys sorted and no insignificant whitespace.
`candidate_hash` is the canonical hash of the validated release candidate.
`policy_hash` is the compiled hash of the package's owner-approved policy.
`issued_at` must not be in the future, `expires_at` must be later than both
`issued_at` and the evaluation time, and the entire approval interval must fit
inside the trusted key's `not_before`/`not_after` interval. A key is unusable at
or after `revoked_at`. Duplicate `(owner_id, key_id)` entries are invalid.

There is no `overall_score` in the release decision. Dimension scores may help
diagnosis but cannot authorize release.

## Rework and adjudication

A blocker ID is append-only identity. Rework must reference the same ID and
provide:

- the candidate diff or new immutable snapshot;
- new evidence;
- a retest report with the original reproduction condition;
- smoke coverage for affected dependent checks; and
- independent adjudication.

An adjudicator may uphold a Fail or clear it after a passing retest. The
adjudicator cannot be the candidate author or either original judge, and cannot
clear a credible Fail by majority opinion.

## Non-deterministic checks

Reliability and agent evaluations retain raw trials. The benchmark record pins
dataset, candidate commit, runner config, environment, attempts, cost, latency,
and token fields. The policy should set a `pass^k` threshold when consistent
success matters; one lucky pass is never sufficient.
