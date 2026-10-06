# MedChat Agent runtime integrity: final delivery report

Date: 2026-10-06
Branch: `fix/agent-runtime-integrity`
Main was not modified, merged, or deployed.

## Delivery status

The requested code and acceptance work is complete on the independent branch.
The default production model registry remains unchanged. The newly trained PDE
and BuChE bundles were verified only in isolated acceptance copies and were not
activated.

## Requirement-by-requirement result

| Requirement | Result | Evidence |
|---|---|---|
| Batch activity CSV column handling | Fixed | `src/web/routes/activity_prediction_routes.py`; whitespace, case, custom names, TSV/BOM, duplicate headers and row order tests |
| Multiline docking parameter parsing | Fixed | `src/agent/planning/request_parsing.py`; parser regression covered by the Agent suite |
| Request temperature propagation | Fixed | validated request configuration is shared by streaming, non-streaming and fallback paths |
| Failed stream fallback duplication | Fixed | `src/web/static/js/home/main.js`; one terminal assistant state is asserted |
| Reverse-target batch completeness | Fixed | `src/web/routes/reverse_target_routes.py`; missing batch rows are failure, valid empty hits remain per molecule |
| Evidence-aware candidate ranking | Fixed | `src/agent/tools/candidate_ranker.py`; missing evidence is explicit and never treated as an advantage or fabricated score |
| Lead-optimization verification | Fixed | `src/agent/tools/lead_optimization_verifier.py`; baseline and candidates use matched metrics and explicit constraints |
| Unified model decision path | Integrated with controlled profiles | `src/web/app.py`, `src/web/decision_runtime.py`, `src/web/decision_chat.py`; legacy remains the safe default and decision mode is explicitly selectable |
| Task evidence, cancellation and resume | Verified | persistent state, failed stream, disconnect and physical cancellation tests |
| Activity feature leakage | Fixed | `deterministic_feature_identity_greedy_v1` split groups source, neutralized feature molecule and feature scaffold identities |
| Real PDE/BuChE training chain | Isolated acceptance passed | v2 packages, four completed training jobs, real-weight classification→regression acceptance |
| LLM settings text cleanup | Fixed | concise settings markup and no-store homepage response; stale old processes must be restarted |

## Verification commands and results

```text
python -m pytest tests/agent -q
12774 passed, 3 skipped, 7 warnings

python -m pytest tests/test_activity_feature_split_integrity.py \
tests/test_activity_dataset_contract.py tests/test_activity_family_dataset.py \
tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py -q
420 passed, 5 skipped, 2 warnings

python -m pytest tests/test_family_training_run.py -q
103 passed, 1 warning

python -m pytest tests/agent/test_activity_contract_integration.py \
tests/agent/test_activity_checkpoint_identity.py tests/agent/test_candidate_ranker.py \
tests/agent/test_lead_optimization_verifier.py \
tests/agent/test_decision_protocol_recovery.py \
tests/agent/test_chat_handler_partial_results.py -q
192 passed

python -m pytest tests/task_runtime/test_docking_consent.py \
tests/task_runtime/test_docking_execution.py -q
280 passed, 2 skipped

python scripts/run_agent_acceptance.py --mode contract
contract: passed

python -m pytest tests/test_activity_family_real_acceptance.py -q
1 passed

node tests/home_llm_settings_test.js
14 passed

node tests/admin_fetch_test.js
passed

python -m compileall -q src scripts
passed
```

The real-weight acceptance used the isolated local run
`pde-buche-20261006-r2`, with bundle IDs supplied through runtime environment
variables. It copied the bundles to fresh temporary directories, checked model
identity and evidence, ran both family paths, and removed the temporary state.

## Real training evidence and limits

The four jobs completed for PDE classification/regression and BuChE
classification/regression using the supplied datasets and threshold 5. The
held-out metrics are retained in the ignored local training report. They are
one feature-aware scaffold split and are not experimental, clinical, docking,
or therapeutic validation.

The new bundles remain under the ignored local model run directory. They are
not part of Git, are not the default registry, and are not automatically used
by the production web entry point.

## Not performed by design

- No real external API key was written to source, `.env`, logs, reports or Git.
- No production model or activity bundle was activated.
- No main-branch modification, merge or deployment was performed.
- ADMET prediction remains unavailable when its configured backend/model asset
  is absent; the system reports that state instead of inventing predictions.
- No scientific accuracy claim is made for docking, ADMET, activity efficacy,
  experimental affinity or clinical outcome.

## Commits on the branch

```text
293c8fb fix: harden agent evidence and workflow contracts
5f93566 fix: prevent stale homepage markup cache
e0d6fec fix: preserve batch activity CSV headers
6adba2d test: guard concise LLM settings markup
096cbd2 fix: align activity splits with feature identities
4a66c8f docs: record real activity acceptance
f824cae docs: record agent lifecycle acceptance
```

The only remaining untracked workspace item is the pre-existing user file
`data/molecular_faiss_index.index.manifest.json`; it was never staged.

## Next controlled action

Before production use, a maintainer should review this report and explicitly
choose whether to register/select the isolated v2 family bundles. That action
is intentionally separate from code acceptance so a green test run cannot
silently change the live model.
