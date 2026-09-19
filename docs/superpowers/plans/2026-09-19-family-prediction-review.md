# Family prediction conflict review implementation plan

> **For agentic workers:** Use subagent-driven-development or executing-plans. Follow the approved sibling spec; no training, activation or production restart.

**Goal:** Preserve raw predictions while marking classification/regression disagreement for review throughout the family prediction chain.

**Architecture:** Add per-row execution_status. Reuse partial for conflicting observations, keep scientific evidence and numeric validation strict. Propagate the distinction through the existing service, Agent, DOM renderer and isolated acceptance instead of adding a new framework.

**Tech Stack:** Python, pytest, FastAPI, existing RG-MPNN inference, native JavaScript and Node DOM tests.

## Task 1: Implement the cross-layer contract with TDD

This is one coupled contract change: Predictor, Validator and consumers must move together.

Files: src/activity/family_predictor.py, src/activity/prediction_service.py,
src/agent/validators/domain_validators.py, src/agent/tools/activity_predictor_tool.py,
src/web/static/js/activity_prediction/results_renderer.js. Modify decision/chat code
only if a regression demonstrates an otherwise unpreserved partial observation.

- [x] Add predictor tests using the existing synthetic stage fixture for both conflict directions and boundaries:

```python
assert row['execution_status'] == 'passed'
assert row['status'] == 'partial'
assert row['success'] is False
assert row['classification_regression_consistent'] is False
assert row['predicted_pIC50'] == original_regression_value
assert row['errors'] == {}
```

- [x] Run `python -B -m pytest tests/test_activity_family_predictor.py -q -p no:cacheprovider`; observe failures caused by the missing contract.
- [x] Update predictor: initialize execution_status=failed; after classification set partial; after regression set passed. Set outcome success/status according to computed consistency, preserve numbers, and append the approved needs-review warning.
- [x] Add summary/API tests for conflict-only and mixed batches, old passed-but-conflicting rows, invalid/empty batches and legacy compatibility. Do not mutate rows in summarize_predictions. Recompute numeric conflict for valid finite probabilities/pIC50 rather than trusting a contradictory marker.
- [x] Add Validator tests: permit only proven dual-output conflict partials; reject forged flags, wrong thresholds, missing provenance, nonfinite values, contradictory stage errors/status. Keep regression-failure partials valid without fabricated pIC50.
- [x] Run `python -B -m pytest tests/test_activity_family_api.py tests/agent/test_family_activity_tool.py -q -p no:cacheprovider` before implementation, then after implementing summary/Validator changes.
- [x] Add Agent and DOM tests: preserve raw rows/provenance/warnings, ObservationStatus.PARTIAL, visible needs-review text, no unconditional completion and no duplicate inference. Use consistent synthetic fixtures for unrelated success tests, explicit conflicts for review tests; do not simply weaken expectations.
- [x] Run `node tests/activity_family_results_test.js` and the focused Agent tests before/after rendering/adapter changes. Keep all added text in safe DOM/text rendering.

## Task 2: Adapt and verify the isolated acceptance boundary

Files: tests/family_acceptance_chain_support.py, tests/family_real_acceptance_support.py,
tests/activity_family_acceptance_dom.js and their existing test modules.

- [x] Add negative tests proving fabricated passed conflict rows and dropped execution_status fail publication validation.
- [x] Project execution_status in the existing bounded report whitelist, and validate against actual per-row consistency and stage outcomes, not the same production summary being tested.
- [x] Change chain assertions to allow engineering checks to pass while the scientific observation remains partial; retain decision/event/WebSocket outcome distinctions and raw numeric comparisons.
- [x] Run `python -B -m pytest tests/test_activity_family_acceptance_chain.py tests/test_activity_family_acceptance_support.py -q -p no:cacheprovider`. Preserve invalid-input and unknown-target rejections and source/cleanup checks.

## Task 3: Independent review, real verification and delivery

- [x] Run all affected Python tests plus `tests/agent`; run Node activity rendering/security tests, `node --check` for changed JS and `python -m compileall -q src scripts`.
- [x] Request independent spec review, resolve findings, then independent quality review. Neither review is replaced by implementer self-review.
- [x] Run the existing opt-in `tests/test_activity_family_real_acceptance.py` with the previously authorized exact PDE/BuChE bundle IDs supplied only as runtime environment settings. No discovery, external model calls, raw training data access or source asset writes.
- [x] Verify source hashes and cleanup, per-row execution/result status, unchanged raw predictions compared with the prior local report, and explicit partial conflict rendering. Report engineering acceptance separately from model consistency/performance.
- [x] Update docs/activity_family_inference.md, docs/activity_family_api.md and docs/handoff/latest.md with accurate results and limitations.
- [x] Explicitly stage only this task's code/tests/docs, commit, create one draft PR targeting main and attach it. Never merge without separate explicit authorization.

## Execution environment and completion criteria

Use the existing MedChat Conda Python (RDKit/PyTorch available), not the default interpreter.
Test fixtures are synthetic unless the opt-in real-weight test explicitly runs; do not label fixture outcomes as scientific predictions.
Completion requires no outstanding correctness findings and truthful failure reporting. Production activation, retraining, threshold changes and model performance claims remain out of scope.
