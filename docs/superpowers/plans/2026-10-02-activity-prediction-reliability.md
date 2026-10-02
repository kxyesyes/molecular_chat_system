# Activity Prediction Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Make activity prediction results, training inputs, model execution, and Agent/UI presentation scientifically auditable and concurrency-safe without replacing the existing family-model or registry architecture.

**Architecture:** Keep `src.activity.prediction_service` as the shared HTTP/Agent boundary, retain `ActivityModelRegistry` and pinned family inference, and add only narrow contract helpers for canonical result fields, immutable request snapshots, strict training labels, layer metadata, and explicit failure status. Update the existing native-JS renderer to consume the canonical contract while preserving compatibility aliases at the boundary.

**Tech Stack:** Python 3.10+, FastAPI, PyTorch/PyG, RDKit, SQLite/filesystem registry, pytest, Node.js static tests.

---

## Task 1: Establish regression coverage for the canonical result contract

**Files:** `tests/test_activity_prediction_contract.py`, `tests/activity_prediction_safe_render_test.js`, `src/activity/prediction_service.py`, `src/web/static/js/activity_prediction/*.js`.

1. Add failing tests for regression (`value`) and classification (`probability`) rendering, missing/unknown confidence, exact inactive labels, and aggregate `passed`/`partial`/`failed` status.
2. Add failing tests that reject fabricated or zero-filled `activity_score` fallbacks.
3. Implement the smallest canonical projection and renderer changes; keep legacy input aliases only at the compatibility boundary.
4. Run focused Python and Node tests.

## Task 2: Strengthen Agent activity validation

**Files:** `tests/agent/test_domain_result_validators.py`, `src/agent/validators/domain_validators.py` or the existing result-validator module.

1. Add failing cases for missing task type, endpoint, units, model ID, weights digest, non-finite value, demo/fallback provenance, and missing evidence.
2. Update only the activity branch of the existing validator to require a finite task-specific numeric field and complete provenance.
3. Preserve existing accepted legacy fixtures by upgrading their test fixtures to the canonical contract, not by weakening validation.

## Task 3: Make request model selection immutable

**Files:** `tests/test_activity_prediction_contract.py`, `src/activity/predictor.py`, `src/activity/prediction_service.py`, and the existing model route only if required.

1. Add a deterministic concurrency test that blocks inference, switches/invalidates the active model, and proves the in-flight request retains one model ID/digest while the next request observes the new snapshot.
2. Capture model, metadata, model ID, digest, and contract data as one immutable inference snapshot before preprocessing/forward.
3. Do not delete or mutate the loaded model object used by an in-flight request; invalidation only affects future snapshots.

## Task 4: Enforce training label and CSV contracts

**Files:** `tests/test_activity_training_contract.py` (new), `src/web/routes/activity_model_routes.py`, `src/activity/trainer.py`, `src/activity/dataset_contract.py` only where a missing invariant is proven.

1. Add failing tests for non-existent selected columns, non-finite labels, illegal classification labels, single-class classification, and continuous labels without an explicit threshold/direction.
2. Add explicit optional threshold/direction form parameters and pass the selected SMILES column through the training path; reject implicit `> 0.5` coercion.
3. Return an explicit failed training status instead of an HTTP success for validation failures.

## Task 5: Align RG-MPNN layer configuration and compatibility metadata

**Files:** `tests/test_rg_mpnn_layers.py` (new), `src/activity/rg_mpnn/Nets/ReduceGNN.py`, `src/activity/predictor.py`, `src/activity/trainer.py`.

1. Add failing tests for one-layer construction/forward and for configured/executed layer counts.
2. Fix the minimum layer boundary without changing existing multi-layer weight shapes.
3. Record a model implementation version and reject incompatible metadata/checkpoint combinations; never reinterpret existing weights silently.

## Task 6: Preserve explicit failure/partial semantics and input parsing

**Files:** `tests/test_activity_training_contract.py`, `tests/test_activity_prediction_contract.py`, `src/web/routes/activity_prediction_routes.py`, `src/activity/predictor.py`, `src/activity/prediction_service.py`.

1. Add failing CSV-header/selected-column and valid no-bond molecule tests.
2. Parse CSV with the selected SMILES column instead of token position; preserve row-level errors and aggregate status.
3. Classify no-bond molecules as explicitly unsupported only when the graph featurizer cannot represent them; do not call them invalid SMILES.

## Task 7: Make split and seed claims reproducible

**Files:** `tests/test_activity_training_contract.py`, `src/activity/trainer.py`, `src/activity/dataset_contract.py`.

1. Add failing tests for Python/NumPy/Torch seed initialization, deterministic flags metadata, and normalized duplicate molecules not crossing train/validation.
2. Seed all relevant RNGs before model initialization and shuffling; record environment and determinism limitations in metadata.
3. Canonicalize split grouping before assignment and keep reproducibility claims conditional on the recorded environment.

## Task 8: Verification, review, and delivery

1. Run focused activity, Agent validator, frontend, compile, and relevant full regression commands in the MedChat environment.
2. Review the diff for unrelated changes, secret leakage, weight/index/output files, and compatibility regressions.
3. Commit only task files on `codex/activity-prediction-reliability`, push, create a PR to `main`, and merge squash only after CI/review are green.
