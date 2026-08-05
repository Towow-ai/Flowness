# FlowBench Results Template

## Result identity

| Field | Value |
|---|---|
| Experiment ID | |
| Protocol version | |
| Source commit | |
| Container / environment digest | |
| Dataset manifest SHA-256 | |
| Evaluator manifest SHA-256 | |
| Provider | |
| Date | |

## Qualification status

- label blindness: PASS / FAIL / NOT RUN
- run-order isolation: PASS / FAIL / NOT RUN
- evaluator-feedback isolation: PASS / FAIL / NOT RUN
- controller-substitution check: PASS / FAIL / NOT RUN
- applicability freeze: PASS / FAIL / NOT RUN

A comparative result is inadmissible when any required qualification channel fails.

## Treatments

Describe each treatment as its native system rather than forcing all systems into the same modality. Record information, authority, tools, human roles, retry limits, and stopping rules.

## Primary outcomes

| Treatment | Task success | Silent-stall rate | False accept | False reject | Correct defer/unknown | Human attention | Tokens | Wall time |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| | | | | | | | | |

## Flow-specific outcomes

| Treatment | Formation | Continuity | Integrity | Adaptation | Commitment | Closure | Learning |
|---|---:|---:|---:|---:|---:|---:|---:|
| | | | | | | | |

## Ablations

Report the effect of removing one mechanism at a time. Do not infer causality from a bundled before/after comparison.

## Failure disclosure

List crashes, invalid runs, evaluator disagreements, missing artifacts, and protocol deviations. Do not silently discard them.

## Claim boundary

State exactly what the batch supports, what it rejects, and what remains unknown. Avoid “better” without naming the metric, treatment set, uncertainty, and scope.
