# Server-owned ADMET Web Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit ADMET requests in the normal Web Agent execute through the real ADMET-AI 1.4.0 tool while preserving the frontend-configured Agent model and the dedicated `gmm-llama:latest` generator role.

**Architecture:** Extend the server-owned scientific admission boundary with a separate `web-admet-v1` profile. The compiler owns exact molecule identity and reviewed tool steps; `WorkflowOrchestrator` and `WorkflowRunSession` remain the execution, provenance, timeout, and cancellation authority. The existing property profile remains unchanged, and mixed property+ADMET requests require both tools to succeed before being reported as completed.

**Tech Stack:** FastAPI, Pydantic 2, existing Pi runtime, `WorkflowOrchestrator`, RDKit input validation, ADMET-AI 1.4.0 subprocess backend, native JavaScript, pytest, and browser acceptance on the started app.

---

## Scope and invariants

- The compiler recognizes ADMET wording only when an explicit action/request is present; explanatory questions remain ordinary chat.
- Valid ADMET input is the exact server-parsed SMILES. No model-generated SMILES, endpoint names, or tool names are authoritative.
- A single-molecule request uses `admet_predictor` with the exact validated SMILES. A mixed request uses `property_calculator` followed by `admet_predictor` with the same input identity.
- `ADMETPredictor` is the only producer of ADMET values. Its existing provenance, model-version, weights, evidence, timeout, unavailable, and partial semantics remain authoritative.
- A required failed, partial, unavailable, timeout, or cancelled ADMET step cannot produce a completed scientific claim.
- The normal Agent model is never replaced by `gmm-llama:latest`; the generator model remains bound only to `llm_molecular_generator`.
- Existing `web-property-v1` behavior and its unsupported-capability rejection tests remain green.

## File map

- Modify: `src/web/scientific_obligations.py` — add an explicit ADMET request detector, profile/version, and reviewed ADMET step semantics.
- Modify: `src/web/decision_runtime.py` — choose the compiler profile and execute the already-compiled ADMET step sequence through the existing session authority.
- Modify: `src/web/decision_chat.py` — project validated ADMET observations into a bounded result card while keeping raw evidence in the audit payload.
- Modify: `src/web/agent_result_presentation.py` only if the existing partial/failure projection needs an additive ADMET step label.
- Test: `tests/agent/test_scientific_obligations.py` — compiler red/green tests for ADMET and mixed obligations.
- Test: `tests/agent/test_web_scientific_execution.py` — real-tool contract tests for successful, unavailable, and mixed-step outcomes.
- Test: `tests/agent/test_decision_result_display.py` or nearest existing display contract — bounded ADMET projection and provenance.
- Test: `tests/frontend_safe_render_test.js` — safe rendering of endpoint values, status, and provenance text.
- Documentation: `docs/PI_RUNTIME.md` or a dated validation note — sanitized browser evidence only after real acceptance.

## Task 1: Lock the compiler contract with failing tests

**Files:** `tests/agent/test_scientific_obligations.py`, `src/web/scientific_obligations.py`.

- [ ] **Step 1: Add the failing tests.**

```python
def test_compiles_admet_request_to_one_reviewed_admet_step():
    plan = ScientificObligationCompiler().compile("请预测 CCO 的 ADMET 特征")
    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.smiles == "CCO"
    assert plan.required_tools == ("admet_predictor",)
    assert plan.steps[0].tool_name == "admet_predictor"
    assert plan.steps[0].input_data == "CCO"


def test_compiles_properties_and_admet_without_dropping_either_obligation():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质和 ADMET 特征"
    )
    assert plan.status == "ready"
    assert plan.workflow_version == "web-admet-v1"
    assert plan.required_tools == ("property_calculator", "admet_predictor")
    assert tuple(step.tool_name for step in plan.steps) == plan.required_tools
    assert all(step.input_data == "CCO" for step in plan.steps)


def test_admet_without_smiles_requests_clarification_without_steps():
    plan = ScientificObligationCompiler().compile("请预测这个分子的 ADMET 特征")
    assert plan.status == "clarification"
    assert plan.missing_fields == ("smiles",)
    assert plan.steps == ()


def test_existing_property_profile_does_not_change():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的分子性质")
    assert plan.status == "ready"
    assert plan.workflow_version == "web-property-v1"
    assert plan.required_tools == ("property_calculator",)


def test_admet_explanation_is_not_forced_into_model_execution():
    plan = ScientificObligationCompiler().compile("请解释 ADMET 是什么")
    assert plan.status != "ready"
    assert plan.required_tools == ()
    assert plan.steps == ()
```

- [ ] **Step 2: Run only these tests and verify RED.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_scientific_obligations.py -q
```

Expected: the new ADMET tests fail because the current compiler marks ADMET as unsupported and has no `web-admet-v1` profile.

- [ ] **Step 3: Implement the smallest typed profile extension.**

Add `"web-admet-v1"` to the plan version literal and add reviewed semantics for `admet_predictor`:

```python
_REVIEWED_TOOL_NAMES = frozenset({
    "property_calculator",
    "drug_likeness_assessment",
    "admet_predictor",
})
_REVIEWED_STEP_SEMANTICS["admet_predictor"] = {
    "name": "admet",
    "output_key": "admet",
    "capability": "molecule.admet",
}
```

The compiler must distinguish the selected profile:

```python
profile = "web-admet-v1" if _admet_request(original_query) else "web-property-v1"
```

For explicit ADMET requests, append `admet_predictor` after `property_calculator` when properties were also requested. Remove only the consumed ADMET marker from the unsupported-capability result; docking, generation, ranking, target-search, reverse-target, and unknown metrics remain rejected. Keep the same RDKit parsing and canonical identity checks for both profiles.

- [ ] **Step 4: Run the compiler tests and related plan regressions.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_scientific_obligations.py tests/agent/test_plan_compiler.py -q
```

Expected: all compiler and existing plan tests pass, with the original property profile unchanged.

## Task 2: Execute ADMET through the existing workflow session

**Files:** `src/web/decision_runtime.py`, `tests/agent/test_web_scientific_execution.py`.

- [ ] **Step 1: Add failing runtime tests using the registered real ADMET adapter.**

Add tests that use the existing temporary Web handler fixture and real `ADMETPredictor` contract. Assert no model decision is needed for a compiled ADMET request:

```python
def test_ready_admet_plan_executes_real_tool_and_publishes_model_provenance(web_handler):
    frames = web_handler.run_turn({"message": "请预测 CCO 的 ADMET 特征"})
    result = result_of(frames)
    assert result["status"] == "completed"
    assert result["metadata"]["workflow_version"] == "web-admet-v1"
    assert result["metadata"]["required_tools"] == ["admet_predictor"]
    observation = result["tool_result_sequence"][0]
    assert observation["tool_name"] == "admet_predictor"
    assert observation["status"] == "succeeded"
    assert observation["provenance"]["tool_name"] == "admet_predictor"
    assert observation["provenance"]["model_version"] == "1.4.0"
    assert "gmm-llama:latest" not in json.dumps(result, ensure_ascii=False)


def test_mixed_property_admet_requires_both_observations(web_handler):
    frames = web_handler.run_turn({"message": "请分析 CCO 的分子性质和 ADMET 特征"})
    result = result_of(frames)
    assert result["metadata"]["required_tools"] == [
        "property_calculator", "admet_predictor"
    ]
    assert [item["tool_name"] for item in result["tool_result_sequence"]] == [
        "property_calculator", "admet_predictor"
    ]
    assert all(item["status"] == "succeeded" for item in result["tool_result_sequence"])
```

If the test environment cannot load ADMET-AI, use the existing explicit unavailable fixture and assert `status in {"unavailable", "failed", "partial"}` with `success is False`; never replace it with a fake successful row.

- [ ] **Step 2: Run the new runtime tests and verify RED.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_web_scientific_execution.py -q
```

Expected: the new ADMET requests are currently rejected with `missing_capabilities=["admet"]`.

- [ ] **Step 3: Extend the compiled bridge without creating a second executor.**

Use `plan.workflow_version` only for metadata and display. Keep the same `WorkflowOrchestrator`, `AgentEventBus`, `settle_owned_call`, cancellation event, worker owner, idempotency key, and `registry.resolve` path. The bridge must dispatch `plan.steps` exactly as compiled; it must not infer or append ADMET steps at runtime.

When resolving `admet_predictor`, require the adapter to return its normal validated `ToolResult`; unavailable or invalid output must flow through the existing terminal failure/partial handling. The generator model must not be passed into this function.

- [ ] **Step 4: Run runtime and lifecycle regressions.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_web_scientific_execution.py tests/agent/test_workflow_status_presentation.py tests/agent/test_admet_ai_integration.py -q
```

Expected: ADMET success/partial/unavailable semantics remain truthful and all existing property lifecycle tests pass.

## Task 3: Add bounded ADMET presentation

**Files:** `src/web/decision_chat.py`, `src/web/agent_result_presentation.py`, `tests/agent/test_decision_result_display.py`, `tests/frontend_safe_render_test.js`.

- [ ] **Step 1: Add failing projection tests.**

Assert that a succeeded ADMET observation renders only bounded, validated fields:

```python
def test_web_scientific_display_projects_admet_provenance_and_endpoints():
    result = make_web_result_for_admet_cco()
    display = web_scientific_display(result)
    assert "ADMET-AI" in display
    assert "1.4.0" in display
    assert "模型预测不等于实验结果" in display
    assert "evidence" in display
    assert "api_key" not in display
    assert "weights_path" not in display
```

The actual test must use the existing validated fixture factory; it must not embed fabricated endpoint values or a raw model path.

- [ ] **Step 2: Run the display tests and verify RED.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_decision_result_display.py -q
node --test tests/frontend_safe_render_test.js
```

Expected: the current projection returns no ADMET display because it only handles property and drug-likeness blocks.

- [ ] **Step 3: Implement the allowlisted ADMET projection.**

Render the molecule identity, model/version, endpoint count, risk count, and each validated endpoint's value/unit/category/source. Render model-boundary text and evidence ID. Do not render arbitrary endpoint metadata, filesystem paths, credentials, raw exceptions, or model-generated prose.

Use existing redaction/sanitization helpers and preserve the structured `final_answer` as the audit authority. A failed or partial observation must remain in the existing failure/partial projection instead of being rendered as a completed ADMET card.

- [ ] **Step 4: Run display and JavaScript regressions.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_decision_result_display.py tests/agent/test_workflow_status_presentation.py -q
node --test tests/frontend_safe_render_test.js tests/home_decision_runtime_test.js
node --check src/web/static/js/home/main.js
```

## Task 4: Real browser acceptance

- [ ] **Step 1: Run offline gates.**

```powershell
.venv-ui-test\Scripts\python.exe -m compileall -q src tests
git diff --check
```

- [ ] **Step 2: Start the application on a fresh test port with the existing frontend configuration.**

Keep the occupied 6001 service untouched. Start a fresh `main.py` instance with the existing `ADMET_AI_PYTHON` configuration and the isolated test state directory. Verify only redacted provider/model metadata.

- [ ] **Step 3: Exercise the browser.**

From the normal homepage, submit:

```text
请预测 CCO 的 ADMET 特征
请分析 CCO 的分子性质和 ADMET 特征
```

Verify visible workflow events, succeeded `admet_predictor`, ADMET-AI 1.4.0 provenance, endpoint/risk summary, and no generator model identity. Verify an invalid SMILES and a deliberately unavailable worker preserve safe failure. Separately confirm the existing direct `/admet` page still succeeds.

- [ ] **Step 4: Record sanitized acceptance evidence.**

Record only status, tool names, model role/version, evidence IDs, and trace prefixes. Do not save raw chat transcripts, API keys, private URLs, or runtime outputs.

## Task 5: Review and delivery

- [ ] **Step 1: Inspect the diff for scope and secrets.**

Confirm only ADMET profile, tests, documentation, and required presentation files changed; do not stage unrelated dirty-tree files, runtime databases, virtual environments, or model assets.

- [ ] **Step 2: Run the final focused commands.**

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_scientific_obligations.py tests/agent/test_web_scientific_execution.py tests/agent/test_decision_result_display.py tests/agent/test_admet_ai_integration.py -q
node --test tests/frontend_safe_render_test.js tests/home_decision_runtime_test.js
.venv-ui-test\Scripts\python.exe -m compileall -q src tests
git diff --check
```

- [ ] **Step 3: Report branch, files, exact test results, browser evidence, uncommitted state, and the known pre-existing full-suite blockers.**
