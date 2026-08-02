# Benchmark protocol

Benchmarking is a reproducible comparison process, not a Star ranking. Stars
may select widely encountered projects for inspection; they do not prove
readiness, architecture quality, or efficacy.

## Comparable-project record

For every project and `observed_at` timestamp, capture independently:

- repository URL, immutable commit or release tag, and license;
- positioning and fit/no-fit from README or official docs;
- install path and a clean-environment transcript;
- test directories, CI configuration, and a local test transcript where
  feasible;
- release cadence and latest inspected release artifact;
- issue and maintenance signals, including sample method;
- architecture, security, governance, upgrade, and rollback surfaces; and
- benchmark datasets, raw results, parameters, cost, latency, and limitations.

README statements remain `documented`, not `verified`, until source, tests,
runtime, or an authoritative postcondition supports them.

## Dimensions

Score each dimension as 0 absent, 1 asserted, 2 documented/example, 3
reproducible, or 4 externally/canonically verified:

1. positioning and fit/no-fit;
2. clean-install first success;
3. architecture and trust boundaries;
4. state, durability, HITL, and recovery;
5. reproducible evidence and disclosed baseline;
6. security, license, governance, and contribution;
7. operation, observability, incident, upgrade, and rollback;
8. examples, cases, and demos;
9. package, CLI, IDE, SDK, and self-host surfaces;
10. release and maintenance;
11. layered explanation and localization; and
12. cross-channel claim consistency.

N/A needs a rationale and is not scored as zero. Critical Flowness readiness
still follows G0-G4 vetoes rather than a benchmark average.

## Reproduction package

Each quantitative comparison pins candidate and comparator commits, dataset and
config hashes, environment, command template, attempts per case, raw trials,
pass@1, pass^k, latency, cost, tokens, date, and known limitations. Compare like
with like; if the systems solve different jobs, record `not_comparable` rather
than forcing a score.
