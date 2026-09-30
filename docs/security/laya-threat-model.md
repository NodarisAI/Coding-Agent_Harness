# Threat model: the Laya decision backend

Changed files: `engine/nodaris_harness/jev.py` (Laya backend), `cli.py` (`laya` command), `onboard.py` (setting
validation), `scripts/laya_dataset.py`, `scripts/laya_eval.py`.

Callers: the UserPromptSubmit hook for a person (human); the `laya` command (human); the dataset and evaluation
scripts (maintainer, run by hand). No service account or background job calls these paths. There are no tenants;
the trust boundary is this machine: a prompt may leave the hook process only to a loopback address.

| Abuse case | Defence | Test |
|---|---|---|
| A setting or environment variable points "Laya" at a remote host, so prompts leave the machine | `laya_endpoint` accepts only http(s) on 127.0.0.1, localhost or ::1 with a port; anything else is None and nothing is sent | `test_laya_is_reached_only_on_this_machine` |
| A prompt with patient identifiers is sent to the local model and ends up in its logs | the same redaction as Jev runs first; a refusal or strong identifier sends nothing | `test_patient_identifiers_never_reach_laya_either` |
| The server answers with injected text that the hook would add to the agent's context | only known choices are accepted by `parse`; anything else adds no line | `test_a_malformed_laya_answer_adds_nothing` |
| A hung or stopped server delays every prompt | a 1.2 s budget per prompt and a breaker after two failures in a row, kept apart from Jev's | `test_a_stopped_laya_server_adds_nothing_quickly_and_opens_its_own_breaker` |
| Training data carries private or identifying text off the machine | the dataset stays under the harness home at mode 0600, private sessions and refused prompts are dropped, output is counts only | `test_the_dataset_keeps_work_prompts_and_drops_private_and_identified_ones` |
| An evaluation run is pointed at a remote host with the held-out prompts | the evaluation uses the same loopback check and exits 2 | `test_evaluation_reports_agreement_and_the_baseline_and_refuses_a_remote_address` |

Residual risk: `laya-serve` binds 0.0.0.0 by default. The harness cannot control how a person starts it; docs/LAYA.md
tells them to bind the loopback address.
