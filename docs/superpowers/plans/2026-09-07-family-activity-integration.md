# Family Activity API / Agent Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Follow TDD and independent specification / quality review.

**Goal:** Connect the approved family classifier → regressor contract to existing API, Agent and result UI without training, activation or deployment.

**Architecture:** Reuse `FamilyActivityPredictor`; a shared service caches it by configured registry directory and summarizes row outcomes. Explicit targets never fall back to the legacy global model. No-target legacy callers remain supported. UI consumes structured rows, preserving partial classification and null regression.

**Tech Stack:** Python/FastAPI, current ToolResult compatibility adapter, native JavaScript DOM, pytest, Node.

## Task 1: Shared service and API (owner)

Files: new `src/activity/prediction_service.py`, `tests/test_activity_family_api.py`; modify only prediction endpoints in `src/web/routes/api_routes.py`.

- [x] Write failing tests for `target=PDE5A`, unknown target, missing bundle, batch ordering, empty result, partial aggregate and threadpool execution.
- [x] Run `python -B -m pytest tests/test_activity_family_api.py -q -p no:cacheprovider` and verify missing functionality fails.
- [x] Implement the shared boundary and route calls:

```python
def predict_activity(smiles, *, target=None):
    if target is not None:
        rows = get_family_predictor().predict(smiles, target=target)
    else:
        from .predictor import get_predictor
        rows = get_predictor().predict(smiles)
    return summarize_predictions(rows)
```

`summarize_predictions` returns `{success, status, results, warnings}`. `passed` requires a nonempty list with every row successful; partial requires at least one successful/partial observation; otherwise failed. Preserve individual errors/provenance without injecting fallback values.

- [x] Add optional `target: str = Form(None)` to both prediction endpoints; invoke the whole service inside existing threadpool. Preserve HTTP errors and use sanitized internal error text.
- [x] Run new tests plus `tests/test_activity_prediction_contract.py`.

## Task 2: Agent tool (bounded implementer)

Files: `src/agent/tools/activity_predictor_tool.py`; new `tests/agent/test_family_activity_tool.py`; a small adjacent input helper if needed. Necessary narrowly scoped planner/orchestrator propagation only after a failing data-flow test.

- [x] Test natural Chinese PDE5A/BuChE target requests, structured inputs, missing model, ambiguous targets, invalid explicit SMILES, inactive regression, partial rows, ToolResult preservation.
- [x] Run new tests RED before production changes.
- [x] Add target-aware path to `execute`; retain no-target legacy contract. Extract complete explicit SMILES, never salvage a valid fragment from malformed input. Use shared service for explicit targets. Format class, probability and pIC50 independently; no numeric substitutions. Preserve rows, warnings, provenance in evidence/quality and aggregate status; never call a family bundle success when incomplete.
- [x] Verify generated candidates retain target intent at the actual invocation boundary; preserve original target alongside downstream SMILES if missing.
- [x] Run new tests and existing activity contract / Agent compatibility tests.

## Task 3: Frontend (owner after API)

Files: `src/web/templates/activity_prediction.html`, `src/web/static/js/activity_prediction/results_renderer.js`, `main.js`; new `tests/activity_family_results_test.js`.

- [x] Add Node DOM behavior tests for passed/partial/failed, zero probability, inactive pIC50, no numeric zero defaults, escaped hostile strings and repeat rendering.
- [x] Run Node tests RED.
- [x] Add single/batch family target input; retain explicit legacy option. FormData submits target, with legacy empty option omitted. Render family rows with DOM `textContent`, separate probability/class/pIC50 and status/warnings; no old global-model gauge or histogram for family predictions. Show failures instead of throwing away rows. Legacy schema stays compatible.
- [x] Run Node tests, existing safety tests, `node --check` for changed JS.

## Task 4: Review / regression / handoff

- [x] Independent specification review, then quality review; add failure regressions for concrete findings.
- [x] Run family/backend/API/Agent focused Python suite, Node suites, `python -m compileall -q src scripts`, `git diff --check`.
- [x] Update `docs/activity_family_models.md` and `docs/handoff/activity-data-readiness.md` with exact results and remaining real-training boundary.
- [x] Precisely stage only task files and commit on `codex/activity-data-readiness`; no original-project edits, PR, main merge, real model activation or service restart.
