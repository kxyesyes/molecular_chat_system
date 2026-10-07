# Agent runtime integrity: current evidence

This is a partial audit, not full-goal acceptance. Work is on
`fix/agent-runtime-integrity`; no production activation or main merge occurred.

## 2026-10-06 activity split repair and real training update

The PDE failure was reproduced rather than bypassed. The prepared dataset used
stereochemistry-aware source Murcko scaffolds, while the RG-MPNN featurizer
strips salts, neutralizes charges, and has no stereochemical identity channel.
That allowed a source molecule/scaffold pair to cross a split and caused the
training-time `Featurized scaffold overlap` guard to stop the run.

`src/activity/dataset_contract.py` now creates connected leakage groups from
source scaffold, neutralized feature molecule, and feature scaffold identities.
The public split algorithm is `deterministic_feature_identity_greedy_v1`.
Prepared artifacts retain the original source `scaffold_smiles` for provenance;
the split assignment is only changed so the actual model inputs remain
disjoint. Existing `deterministic_scaffold_greedy_v2` snapshots remain readable
for compatibility, but the training-time feature guard still refuses a leaked
legacy snapshot.

New local-only packages were prepared without overwriting the previous assets:

- `pde-family-v2`: 2642 accepted unique molecules from 4012 rows;
- `buche-family-v2`: 2285 accepted unique molecules from 2361 rows, with 26
  ambiguous multifragment rows rejected.

The real CUDA run `pde-buche-20261006-r2` completed all four jobs in an
isolated run directory. The held-out metrics are recorded in the ignored local
report; they are one scaffold/feature-split baseline, not experimental or
clinical validation. The bundles were not activated. The training runner now
accepts `--package-version v2` while retaining `v1` as its default for older
callers.

Focused verification after the repair:

```text
python -m pytest tests/test_activity_feature_split_integrity.py tests/test_activity_dataset_contract.py tests/test_activity_family_dataset.py tests/test_activity_prepared_training_data.py tests/test_activity_prepared_training_loop.py -q
420 passed, 5 skipped, 2 warnings

python -m pytest tests/test_family_training_run.py -q
103 passed, 1 warning
```

The full Agent regression and the opt-in real-weight family acceptance then
passed without changing the production registry:

```text
python -m pytest tests/agent -q
12774 passed, 3 skipped, 7 warnings

MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=1 \
MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR=data/activity/models/pde-buche-20261006-r2 \
MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID=pde-buche-20261006-r2-pde-family \
MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID=pde-buche-20261006-r2-buche-family \
python -m pytest tests/test_activity_family_real_acceptance.py -q
1 passed
```

The real-weight acceptance copied each family bundle into a fresh owned
temporary directory, ran the classification-to-regression chain, checked the
bound model identity and cleaned the directory. It is isolated acceptance
evidence, not a production activation or an experimental accuracy claim.

Additional end-to-end boundary checks passed after the full Agent suite:

```text
python scripts/run_agent_acceptance.py --mode contract
contract: passed

python -m pytest tests/agent/test_activity_contract_integration.py \
tests/agent/test_activity_checkpoint_identity.py \
tests/agent/test_candidate_ranker.py \
tests/agent/test_lead_optimization_verifier.py \
tests/agent/test_decision_protocol_recovery.py \
tests/agent/test_chat_handler_partial_results.py -q
192 passed

python -m pytest tests/task_runtime/test_docking_consent.py \
tests/task_runtime/test_docking_execution.py -q
280 passed, 2 skipped
```

These checks cover required-tool/evidence identity, matched baseline/candidate
comparisons, failed-stream projection, persistent task state, disconnect and
physical cancellation cleanup. They do not activate a production model or
claim docking/ADMET scientific accuracy.

## Batch activity upload: missed route fixed

The earlier training-column fix did not fix the batch prediction endpoint.
`src/web/routes/activity_prediction_routes.py::_parse_batch_smiles` stripped
headers before matching, then used that stripped value to read a `DictReader`
row whose keys still contained whitespace. Valid structures became empty strings.

The minimal fix retains original header keys and normalizes only matching.
Duplicate matching headers remain an HTTP 400 error before inference.

Real HTTP upload tests in `tests/test_activity_family_api.py` cover whitespace,
case, explicit custom selection, CSV/TSV, UTF-8 BOM, duplicate headers, row order,
repeated molecules, and an invalid molecule. They use the actual parsing and
prediction service with an isolated empty registry, not real trained inference.
Missing models must still return unavailable values, never simulated activity.

Red command:

```text
python -m pytest tests/test_activity_family_api.py -k "resolves_original_header or duplicate_matching_headers" -q
```

Before fix: **3 failed, 4 passed, 72 deselected**; whitespace/custom/TSV rows
became empty. After fix, the following combined command passed:

```text
python -m pytest tests/test_activity_family_api.py tests/test_activity_prediction_reliability.py tests/test_activity_csv_column_resolution.py -q
```

Result: **97 passed, 1 warning** (PyG deprecation).

## Real training attempt (local assets only)

Run `pde-buche-20261006-r1` used the existing frozen training runner and the
user-supplied datasets; label threshold remains 5. No model was activated.
Prepared packages, weights and reports are ignored local assets, not Git inputs.

Preparation accepted 2642 unique PDE molecules from 4012 rows and 2285 unique
BuChE molecules from 2361 rows. BuChE rejected 26 ambiguous multifragment rows.
Both packages passed the existing preparation contract.

The training run ended **partial**:

- PDE classification and regression failed before the first epoch.
- A bounded diagnostic, with fitting disabled, reproduced
  `Featurized scaffold overlap between train and validation; refusing training`
  in `prepared_training._claim_feature_identity`. Preparation-time splitting
  and predictor-time molecular preprocessing do not establish the same scaffold
  separation. This is an open issue; the leakage guard remains unchanged.
- BuChE classification and regression completed and formed a registered bundle
  only in the isolated run directory. Held-out classification ROC-AUC was
  0.8000834434, PR-AUC 0.8210887775 and balanced accuracy 0.7507301300.
  Held-out regression RMSE was 0.9167719575, MAE 0.7272658856 and R2 0.2462806274.
  These are one scaffold-split baseline, not experimental/clinical validation.
- Joint classifier/regressor consistency was not evaluated. Do not infer
  end-to-end prediction acceptance or production readiness from training completion.

The default model registry remains empty. Its old sidecars fail current metadata
validation; they must not be force-registered merely to make health checks green.

## Remaining before full goal acceptance

1. Produce the final requirement-by-requirement report, including explicit
   non-claims for unactivated production models, unavailable ADMET assets and
   unverified docking/experimental accuracy.
2. Decide whether to promote the isolated v2 activity bundles through the
   normal reviewed activation path; no promotion is implied by the acceptance
   results above.
