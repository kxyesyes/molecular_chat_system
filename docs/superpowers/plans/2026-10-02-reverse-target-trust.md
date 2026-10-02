# Reverse Target Scientific Trust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make reverse-target results scientifically defensible, reproducible, and auditable from ChEMBL ingestion through 2D/3D scoring, batch APIs, exports, and browser interactions.

**Architecture:** Preserve the existing FastAPI + native JavaScript + RDKit design. Add a small, shared data-quality layer for unit conversion, identity-aware deduplication, provenance, and numeric validation; make the predictor consume the richer rows without changing its public entry points. Make 3D refinement return explicit status and score provenance, and make route/UI layers pass request-scoped inputs instead of global state. Keep compatibility fields where existing clients depend on them, while adding explicit scientific fields and rejecting unsafe inputs.

**Tech Stack:** Python 3.10+, Pandas, SQLite/ChEMBL, RDKit, NumPy, FastAPI, pytest, native JavaScript, Node.js static tests.

---

## Phase 1: P0 data quality and audit fields

### Task 1: Add failing unit-cleaning and identity-deduplication tests

**Files:**
- Create: `tests/test_reverse_target_data_quality.py`
- Inspect: `src/reverse_target/extract_clean_data.py`
- Inspect: `src/reverse_target/fetch_chembl_api.py`

- [ ] Write tests for confirmed `pM`, `nM`, `uM`, `mM`, and `M` conversion; unknown, blank, and missing units must be rejected/marked rather than treated as nM. Assert preservation of `standard_value_raw`, `standard_units_raw`, `unit_conversion_factor`, `unit_conversion_status`, and `standard_value_nm`.
- [ ] Write tests showing two same-named targets from different `target_chembl_id`/UniProt/taxon survive deduplication, while the same molecule-target-assay-publication measurement is deduplicated.
- [ ] Write tests showing `standard_relation != '='`, invalid `data_validity_comment`, `potential_duplicate`, and assay confidence below 8 are retained as provenance but excluded from the default training-quality view.
- [ ] Run `C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_reverse_target_data_quality.py -q -p no:cacheprovider`; confirm the new tests fail for the current implementation.

### Task 2: Implement shared ChEMBL quality helpers and route both ingestion paths through them

**Files:**
- Create: `src/reverse_target/data_quality.py`
- Modify: `src/reverse_target/extract_clean_data.py`
- Modify: `src/reverse_target/fetch_chembl_api.py`
- Test: `tests/test_reverse_target_data_quality.py`

- [ ] Implement `convert_confirmed_unit(value, unit)` returning converted value, original unit, factor, and a status; accept only explicit aliases for molar units and return an unusable status for unknown/missing units.
- [ ] Implement `deduplicate_activity_rows(frame)` with identity dimensions: molecule ID, target ChEMBL ID or UniProt, organism/taxon, assay ID, publication/document ID, standard type, and normalized measurement; use target name only as a last-resort identity component.
- [ ] Implement `apply_activity_quality_flags(frame)` preserving ChEMBL fields and producing `quality_eligible`, `quality_reasons`, and `provenance` columns. Default eligibility requires finite positive converted nM, relation `=`, no invalidity comment, no potential duplicate, and assay confidence 8/9 when present.
- [ ] Update SQL/API extraction to select target IDs, UniProt/taxon, assay/document IDs, relation, validity, duplicate, and confidence when available; tolerate older fixtures by filling missing optional columns with null, never with scientifically meaningful defaults.
- [ ] Replace unknown-unit passthrough and `unit='nM'` default with explicit rejection/flagging. Keep raw value/unit and conversion metadata in TSV output.
- [ ] Make `format_training_data` write only `quality_eligible` rows to the default training TSV while also writing a complete cleaned/provenance TSV for audit.
- [ ] Rerun the data-quality tests and existing reverse-target health tests.

### Task 3: Preserve provenance and quality fields in fingerprint generation and predictor results

**Files:**
- Modify: `src/reverse_target/generate_fingerprints.py`
- Modify: `src/reverse_target/predictor.py`
- Modify: `tests/test_reverse_target_data_quality.py`
- Modify or create: `tests/test_reverse_target_predictor_quality.py`

- [ ] Add fingerprint parameter/version metadata and source-data SHA256 to the fingerprint manifest.
- [ ] Ensure fingerprint filtering preserves original row identity (`source_row_index`) and all target/assay/provenance fields.
- [ ] Include `target_chembl_id`, `uniprot_id`, `taxon_id`, `assay_id`, `document_id`, quality flags, raw value/unit, and `source_row_index` in 2D result records.
- [ ] Rename public interpretation fields to `similarity`/`rank` semantics and retain compatibility aliases only where existing UI/tests require them; never emit `probability` or `confidence` for a 2D similarity score.
- [ ] Add a deterministic result manifest containing normalized input SMILES, fingerprint parameters, thresholds, weights, source data version, and current commit when available.

## Phase 2: 2D evaluation semantics and safe 3D refinement

### Task 4: Add metric and calibration evaluation utilities

**Files:**
- Create: `src/reverse_target/evaluation.py`
- Create: `tests/test_reverse_target_evaluation.py`
- Modify: `src/reverse_target/predictor.py` only if result metadata needs a shared scorer

- [ ] Write failing tests for scaffold split and time split with deterministic grouping, Top-K recall, MRR, enrichment factor, BEDROC, PR-AUC, and reliability-bin calibration output.
- [ ] Implement metrics as evaluation-only utilities that consume labeled predictions and never re-label similarity as probability.
- [ ] Require explicit labels/ground truth for calibration; return `not_available` with a reason when labels are absent.
- [ ] Add evaluation metadata: split method, seed, fingerprint configuration, source version, and commit.

### Task 5: Add bounded pharmacophore matching and strict 3D conformer status

**Files:**
- Modify: `src/reverse_target/pharmacophore_refiner.py`
- Create or modify: `tests/test_reverse_target_pharmacophore_quality.py`

- [ ] Write failing tests for an inner matching deadline, cancellation callback, MMFF missing parameters, non-convergence, and empty conformer results.
- [ ] Replace exhaustive unbounded backtracking with a deadline-aware bounded beam/greedy matching algorithm whose inner loop checks a monotonic deadline/callback.
- [ ] Make `generate_3d_conformer` record embedding method, force-field name, parameter availability, optimization return codes, convergence, selected conformer ID, and failure reason. A missing parameter or non-converged result must return `success=False`.
- [ ] Add explicit per-family feature cutoffs, including a documented `LumpedHydrophobe` cutoff; never fall back to a generic 1.8 Å cutoff for that family.
- [ ] Add `prefilter_threshold` naming at the route/refiner boundary; retain `adjusted_threshold` only as a compatibility field marked as prefiltering.
- [ ] Return `pharm_refinement_status` values that distinguish `refined`, `not_refined`, `timeout_fallback`, `fallback`, and failure; attach `pharm_error` and `pharm_method`.

### Task 6: Prevent mixed-score ranking and misleading 3D labels

**Files:**
- Modify: `src/reverse_target/pharmacophore_refiner.py`
- Modify: `src/web/routes/reverse_target_routes.py`
- Modify: `src/web/static/js/reverse_target/results_renderer.js`
- Modify: `src/web/static/js/reverse_target/viewer_3d.js`
- Test: `tests/test_reverse_target_pharmacophore_quality.py`, `tests/test_reverse_target_frontend_static.py`

- [ ] Write failing tests proving refined candidates use `0.4 * similarity + 0.6 * 3d_score`, while unrefined/fallback candidates are not mixed into that ranking; return separate `refined_results` and `unrefined_results` or a single list with an explicit comparable-score field and status partition.
- [ ] Implement deterministic ranking partitions and expose `score_type`, `similarity_rank`, `combined_3d_score`, and `comparability`.
- [ ] Update response messages and UI badges so fallback/not-refined entries say `2D fallback`/`未精修` with the reason, never `3D 综合` or `高可信`.
- [ ] Ensure no score is labeled probability/confidence unless produced by a calibrated evaluator with evidence.

## Phase 3: API and batch data integrity

### Task 7: Add strict route validation and robust CSV parsing

**Files:**
- Modify: `src/web/routes/reverse_target_routes.py`
- Create or modify: `tests/test_reverse_target_routes_quality.py`

- [ ] Write failing API tests for NaN/Inf, threshold and alpha outside 0–1, alpha sum policy, negative/zero/over-limit values, missing SMILES column with row/column details, uppercase `.CSV`, and more than 100 rows.
- [ ] Implement shared finite-number/range validation for 2D and 3D routes; require alpha weights to sum to 1 within tolerance or normalize and report the normalization explicitly.
- [ ] Parse suffixes using `Path(filename).suffix.lower()`; require a named SMILES column for CSV and report the missing column plus row numbers rather than silently using the first column.
- [ ] Preserve input order and duplicates as rows using `original_row_index`; deduplicate only for computation with a stable ordered map, then fan results back out to every input row.
- [ ] Return complete batch records for success, failure, and no-match, each with `status`, `error`, `original_row_index`, `query_smiles`, and refinement fields.
- [ ] Offload predictor loading and similarity/stats work through `_invoke_in_threadpool` consistently.

### Task 8: Make detail requests explicit and auditable

**Files:**
- Modify: `src/web/static/js/reverse_target/api_client.js`
- Modify: `src/web/static/js/reverse_target/results_renderer.js`
- Modify: `src/web/static/js/reverse_target/main.js`
- Modify: `src/web/static/js/reverse_target/ui_manager.js`
- Test: `tests/reverse_target_broad_recall_test.js`, `tests/reverse_target_pagination_test.js`, create `tests/reverse_target_batch_state_test.js`

- [ ] Write failing Node tests showing batch detail calls use row `query_smiles`, `threshold`, target ID, and source row index, not `window.currentQuerySmiles` or `lastThreshold`.
- [ ] Pass a request-scoped detail object through renderers and API client; only use global state for legacy single-query fallback when no row context exists.
- [ ] Include target ID/UniProt and original row in detail requests and displayed provenance.
- [ ] Preserve stable batch order, duplicate rows, and no-match/error rows in UI and export state.

### Task 9: Add request sequencing and complete export fields

**Files:**
- Modify: `src/web/static/js/reverse_target/api_client.js`
- Modify: `src/web/static/js/reverse_target/results_renderer.js`
- Modify: `src/web/static/js/reverse_target/viewer_3d.js`
- Modify: `src/web/static/js/reverse_target/ui_manager.js`
- Create or modify: `tests/reverse_target_request_race_test.js`, `tests/reverse_target_batch_state_test.js`

- [ ] Write failing tests for out-of-order A/B responses for 3D viewer, similar modal, and MCS; assert only the latest request updates the DOM.
- [ ] Add request sequence IDs and `AbortController` cancellation to those paths; ignore stale responses and stale errors.
- [ ] Expand CSV export to include every input row and `status`, `error`, `original_row_index`, `pharm_refinement_status`, `pharm_error`, `data_version`, fingerprint parameters, thresholds, weights, and commit.
- [ ] Escape all exported values and keep failed/no-match rows rather than dropping them.

## Phase 4: Verification and integration

### Task 10: Regression, audit manifest, and PR integration

**Files:**
- Modify: `src/reverse_target/receipt.py` or the existing receipt integration point if needed
- Modify: `tests/test_reverse_target_invocation_receipts.py`
- Modify: `docs/reverse_target/` documentation if an existing page exists; otherwise create `docs/reverse_target_quality.md`

- [ ] Add receipt assertions for normalized SMILES, data version, source SHA256, fingerprint parameters, threshold/weights, 3D state, random seed, and commit.
- [ ] Run focused reverse-target tests, all relevant Node tests, `compileall`, and the full Python suite in the MedChat Conda environment.
- [ ] Run `git diff --check`, inspect the diff for secrets/generated assets, and verify only task files are staged.
- [ ] Commit by phase, create one PR per coherent phase, run CI, obtain independent review, and squash merge only after the current repository rules and explicit/default authorization permit it.

## Verification commands

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_reverse_target_data_quality.py tests/test_reverse_target_predictor_quality.py tests/test_reverse_target_pharmacophore_quality.py tests/test_reverse_target_routes_quality.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_reverse_target_health.py tests/test_reverse_target_pharmacophore.py tests/test_reverse_target_invocation_receipts.py tests/test_reverse_target_popcounts.py -q -p no:cacheprovider
node tests/reverse_target_broad_recall_test.js
node tests/reverse_target_pagination_test.js
node tests/reverse_target_batch_state_test.js
node tests/reverse_target_request_race_test.js
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

## Self-review and known compatibility boundaries

- The plan keeps existing endpoint paths and compatibility aliases but changes unsafe scientific interpretation fields and rejects unknown units by design.
- Existing local datasets with only `target_name` or missing quality columns will remain readable for health/inspection, but cannot be marked training-eligible until provenance fields are available.
- A database rebuild is required to materialize the new provenance columns and aligned fingerprints; no generated database, fingerprint matrix, or cache will be committed.
- Probability calibration is an evaluation artifact only; production 2D similarity remains a similarity/rank unless a labeled calibration model is explicitly present.
