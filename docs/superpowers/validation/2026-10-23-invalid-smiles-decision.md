# Invalid-SMILES decision repair validation

## Scope and root cause

The real DeepSeek reproduction returned a scientific finish with zero evidence
IDs after the real RDKit property tool rejected invalid SMILES. The strict
validator correctly returned `invalid_decision_schema` at `decision.finish`.
After schema correction the model clarified, but the unchanged acceptance gate
correctly kept the case failed because one model decision had failed validation.
A separate standalone probe clarified without the error: this is intermittent
provider behavior, not a deterministic SMILES parser or validator defect.

The initial prompt omitted rules already included in schema correction. The fix
moves explicit clarification/evidence guidance into the initial instructions,
distinguishes missing input from tool-confirmed invalid input, and preserves the
existing server-authorized family-disagreement review-only completion path.
No schema, validator, acceptance, tool authorization, network policy, or retry
budget was changed. No deployment or merge was performed.

## Offline checks

- Before the production edit: new regressions **5 failed, 2 passed**, with failures
  specifically on missing initial prompt instructions.
- Initial minimal edit: **7 passed**.
- Review compatibility tests before refined wording: **6 failed, 4 deselected**.
- Final focused invalid input, prompt, valid RDKit, and family-review tests:
  **21 passed, 75 deselected**.
- Unchanged baseline loop/acceptance selection: **134 passed, 1 skipped**.
- Separate existing network-security/runtime-boundary selection: **36 passed**.
- `python -m compileall -q src scripts`: passed on final source.
- `git diff --check`: passed.
- Final complete affected selection: **576 passed, 1 skipped, 7 warnings** in
  555.25 seconds. The skipped directory-symlink report-path test requires directory
  symlink creation unavailable on this machine; it was not counted as passed.

Final affected selection command:

```powershell
python -B -m pytest tests/agent/test_invalid_smiles_decision.py tests/agent/test_decision_loop.py tests/agent/test_decision_chat_acceptance.py tests/agent/test_decision_protocol_recovery.py tests/agent/test_decision_contract.py tests/agent/test_decision_binding_loop.py tests/agent/test_decision_chat.py tests/agent/test_decision_transport_boundaries.py tests/test_agent_decision_model.py tests/test_llm_security_policy.py tests/test_runtime_boundary_contracts.py -q --disable-warnings
python -m compileall -q src scripts
```

The deterministic HTTP fixtures verify prompt delivery, real RDKit rejection,
protocol handling and strict failure accounting. They do not demonstrate real
model behavior. The live checks below are separate.

## Real DeepSeek verification

Authorized model: `deepseek-v4-pro`, native protocol, `api.deepseek.com`.
Used the unchanged `scripts.run_decision_chat_acceptance.run_acceptance` entry,
actual `ChatHandler`/Pi runtime, temporary SQLite state and actual RDKit property
tool. URL policy, public-address resolution, pinned transport, TLS checks and
strict acceptance gates remained enabled. Keys were loaded locally into memory;
no keys, raw model replies, or private reasoning were printed or recorded.

| Revision / run | Result | Model decisions | Input / output / total tokens |
| --- | --- | --- | --- |
| Unchanged isolated invalid-input probe | Clarified; actual tool rejected input | 2 | 3,581 / 174 / 3,755 |
| Unchanged four-case reproduction | 3/4; empty-evidence finish reproduced | 9 | 17,777 / 1,021 / 18,798 |
| Initial prompt fix, run 1 | 4/4 | 8 | 16,121 / 918 / 17,039 |
| Initial prompt fix, run 2 | 4/4 | 8 | 16,121 / 916 / 17,037 |
| Final reviewed prompt, run 1 | 4/4 | 8 | 16,769 / 901 / 17,670 |
| Final reviewed prompt, run 2 | 4/4 | 8 | 16,755 / 902 / 17,657 |

Both final runs passed all unchanged checks for ordinary chat, two-molecule
properties, missing-input clarification/resume, and invalid SMILES. Invalid
SMILES used one real property-tool rejection followed by clarification, with no
schema failure and no fabricated properties. Valid-molecule subject/metric
acceptance passed. All 16 final model decisions validated successfully.

Final reruns used **35,327 tokens**. This repair task including diagnostic and
intermediate runs used **43 model decisions / 91,956 tokens** with usage reported
for every call. Monetary cost is not calculated; provider billing applies.

Sanitized detailed reports remain local and ignored, not committed:

- `outputs/agent_evaluation/pi_deepseek_20261023_invalid_smiles_fix_run1.json`
- `outputs/agent_evaluation/pi_deepseek_20261023_invalid_smiles_fix_run2.json`
- `outputs/agent_evaluation/pi_deepseek_20261023_invalid_smiles_final_run1.json`
- `outputs/agent_evaluation/pi_deepseek_20261023_invalid_smiles_final_run2.json`

## Limits, compatibility and rollback

Two passing final samples are not proof that a probabilistic provider can never
emit malformed output. Existing strict validation and bounded repair remain the
defense. This is isolated ChatHandler acceptance, not production browser or full
scientific-platform acceptance. No full repository suite, JS check, deployment
health check, real activity/reverse-target/docking or full-Web live acceptance
was run for this prompt-only change. No JS, deployment or data files changed.
Existing source checkout edits and unrelated worktree files were preserved.

An independent review identified the tool-validation wording and review-only
compatibility boundaries; both were checked against the implementation and
covered before final real-provider reruns. Rollback is reverting the prompt
change; there is no migration or API/schema compatibility change.
Independent re-review found no remaining actionable issues in the scoped change.
