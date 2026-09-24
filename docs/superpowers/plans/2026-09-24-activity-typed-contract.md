# Activity Adapter Typed Contract Implementation Plan

> **For agentic workers:** Use subagent-driven-development to implement this plan, with independent specification then quality review. Checkbox syntax tracks steps.

**Goal:** Give only `activity_predictor` a runtime input/output contract without changing scientific algorithms, public results, or worker lifecycle.

**Architecture:** A dedicated non-projecting validation adapter surrounds the existing legacy compatibility adapter. Reuse the activity parser, domain validator, summary function, and metadata validator. Factory selects this adapter only for activity. No model initialization in validation.

**Tech Stack:** Python 3.10, Pydantic 2 (including minimum 2.5.0), pytest, existing ToolRegistry and ToolResult.

Approved specification: `docs/superpowers/specs/2026-09-24-activity-typed-contract-design.md`. Implementation worktree: `D:/MedChat/molecular_chat_system_worktrees/activity-typed-contract-pr`, branch `codex/activity-typed-contract-pr`. No main edits, deployment, training or real API calls.

## Task 1 — RED then minimal activity contract

Files:
- Create `tests/agent/test_activity_tool_contract.py`.
- Create `src/agent/tooling/activity_contract.py`.
- Modify only activity selection in `src/agent/tooling/factory.py`.
- Update `tests/agent/test_registration_consistency.py` only if existing generic-schema assertions require it.

- [x] Write tests using an explicit recording tool via the real `build_tool_registry`, closing registry resources in `finally`/fixtures. First assert an activity-specific schema exists and that `{"query":"text", "smiles":["CCO","CCO"], "target":"PDE5A"}` reaches the tool intact. Record failing command before implementation.
- [x] Parametrize string, query wrapper, one-level delegated wrapper, structured missing-query input; preserve extras inside scientific payload and duplicates. Reject None/bool/bytes/object/list coercions, nested ambiguity and constructed-invalid model instances without executing the tool.
- [x] Add strict envelope validation views for raw and canonical results: bool success, legal status, activity identity, typed supplied metadata. Use original ToolResult and raw dictionaries after validation, never projected `model_dump()` as output.
- [x] Reuse `ActivityResultValidator` and `summarize_predictions` for family rows. Test complete, conflict partial, regression-failure partial, all-null failure and malformed rows in all three statuses. Keep row warning compatibility.
- [x] Validate actual targetless regression/classification rows, finite values and probability range. Reuse `_validate_prediction_metadata`, require explicit non-demo provenance, reject fake success and empty success. Keep original errors and mixed legacy batches; never invent pIC50 or metadata.
- [x] Compose caller raw validation and private contract failures; fixed safe error messages must not echo payloads. Keep `execute_tool_compat` as sole normalization path. Assert valid old failures preserve current `raw_result` placement.
- [x] Add registry lifecycle assertions: execute exactly once, caller validator once, unavailable/timeout/capacity unchanged, close and worker release retained.
- [x] Run focused tests to GREEN, then original four-file 354-test baseline. Fix only contract/fixture incompatibilities supported by evidence. No weakening scientific checks.

Concrete test shape (adapt registry lookup to its actual API):
```python
payload = {"query": "预测", "smiles": ["CCO", "CCO"], "target": "PDE5A"}
result = adapter.execute(payload)
assert recorder.inputs == [payload]
assert result.data == original_result.data
```
An invalid output test must assert `result.error.code == AgentErrorCode.INVALID_OUTPUT`, and that serialized error details exclude an injected sensitive marker.

## Task 2 — delegated integration and regression (disjoint test file)

Create `tests/agent/test_activity_contract_integration.py`; read the existing specialist dispatch and activity fixtures, no production edits outside Task 1.

- [x] Exercise actual Activity specialist/dispatch wrapping with a recording ActivityPredictorTool-compatible stub and explicit synthetic provenance, comparing received query/SMILES/target to input.
- [x] Exercise actual ActivityPredictorTool parser and domain validation with injected service only: invalid whole SMILES and ambiguous targets must not invoke prediction; canonical family full/partial/failed results survive adapter unchanged.
- [x] Ensure test fixtures distinguish explicit synthetic model metadata from real weight validation. No services, checkpoints, API keys or repository runtime configuration loaded.
- [x] Run focused integration tests; a failure before Task 1 integration is expected only for missing contracts, not import/environment problems.

## Task 3 — review, evidence and handoff

- [x] Independently review specification compliance, then code quality; fix verified issues with new failing regression first. Reviewers do not write the same implementation files concurrently.
- [x] Run all Agent tests plus activity/web lifecycle/anti-hallucination tests using the existing isolated runner with only the worktree path replaced. Do not run that wrapper across all target database tests: its global TARGET paths override per-test roots.
- [x] Run offline contract mode with real/canary disabled; block external network. Compile tracked src/scripts in memory; `git diff --check`.
- [x] Record exact commands, RED/GREEN counts, skips, warnings and remaining issues in `docs/handoff/activity-typed-contract.md`. A typed-contract pass is not scientific real-model acceptance.
- [x] Explicitly stage only approved task files, commit on task branch. No pushing/merging without applicable publication authority and required CI/reviews.

## Validation commands

Use `C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest ... -q -p no:cacheprovider --tb=short -rs` through the isolation wrapper documented in `2026-09-24-rag-service-extraction.md`, replacing its repo path with this worktree. Wrapper clears inherited non-whitelisted environment, uses temporary cwd/config/databases, disables real/canary, and copies only tracked evaluation fixtures with hash checks.

Focused paths:
```text
tests/agent/test_activity_tool_contract.py
tests/agent/test_activity_contract_integration.py
tests/agent/test_family_activity_tool.py
tests/agent/test_activity_input_boundaries.py
tests/test_activity_prediction_contract.py
tests/agent/test_registration_consistency.py
```

Joint paths: `tests/agent`, `tests/test_activity_prediction_contract.py`, `tests/test_agent_anti_hallucination_fallbacks.py`, `tests/test_agent_platform_health_check.py`. Preserve timeout/resource semantics; do not loosen timing assertions to make this branch pass.

## Plan self-review

All five input forms, scientific and transport validation, legacy canonicalization, exact metadata preservation, delegated routing, worker ownership and safe errors map to the approved design. Production write set is two files; integration tests are disjoint and can proceed alongside implementation. Legacy Agent tuple/ToolResult defects, target-test isolation, Windows CLI encoding, docking contracts and Planner refactors remain separate work.
