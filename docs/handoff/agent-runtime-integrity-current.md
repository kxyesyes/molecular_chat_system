# Agent runtime integrity: current evidence

This is a partial audit, not full-goal acceptance. Work is on
`fix/agent-runtime-integrity`; no production activation or main merge occurred.

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

## Still required before full goal acceptance

1. Resolve or explicitly disposition the preparation/feature scaffold mismatch
   without relaxing leakage checks, overwriting existing packages or silently
   dropping rows; do not automatically repeat successful training jobs.
2. Audit evidence identity and required-goal enforcement through the full
   candidate-ranking and lead-optimization flows, not only individual tools.
3. Verify request parameters and protocol semantics on both legacy and model
   decision paths, including failed stream fallback and persistent message state.
4. Verify status lookup after disconnect and physical cancellation semantics
   across actual long-running scientific tasks; passing wrapper tests alone is
   not proof that a computation stopped.
5. Finish the bounded, isolated real-chain acceptance and deliver a requirement-
   by-requirement report. Previous broad pytest counts do not prove these missing
   end-to-end requirements and must not be used as a completion claim.
