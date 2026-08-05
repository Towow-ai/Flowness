# Failure Clinic Fixture Template

Use this template to turn a dogfood incident into a replayable public case.

```yaml
fixture_id: FC-YYYY-NNN
status: draft | qualified | published | retired
failure_family: formation | continuity | integrity | adaptation | commitment | closure | learning
source_class: public-reproduction | sanitized-dogfood | synthetic-counterexample
claim_scope: "What this fixture can and cannot establish"

world:
  target_object: "exact object and version"
  initial_state: {}
  external_dependencies: []
  authority_and_owner: []

trigger:
  event: "What changed"
  expected_system_response: "What a healthy Flow should do"

mechanism_off:
  configuration: {}
  observed_failure: "The failure reproduced"
  evidence_refs: []

mechanism_on:
  configuration: {}
  observed_capture_or_repair: "What changed"
  evidence_refs: []

controls:
  same_information: true
  same_target: true
  same_model_or_deterministic_executor: true
  no_expected_path_in_evaluator: true

metrics:
  detected: null
  time_to_detection_ms: null
  time_to_recovery_ms: null
  human_attention_seconds: null
  token_cost: null
  false_accept: null
  false_reject: null

remaining_boundary:
  - "What the mechanism still does not solve"

release_binding:
  source_commit: null
  fixture_sha256: null
  evidence_manifest_sha256: null
```

## Publication gate

A fixture is publishable only when:

- the failure is independently replayable;
- mechanism-off and mechanism-on differ only in the declared mechanism;
- the evaluator does not encode the expected path or answer;
- exact target/version and evidence lineage are visible;
- negative or mixed outcomes are retained;
- the claim is no broader than the fixture.
