# Target Structure + Molecule Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Allow one safe Web request to run target-structure lookup plus explicit-SMILES properties/ADMET, preserving partial scientific evidence.

**Architecture:** Add a small deterministic request classifier and workflow policy. The classifier admits only one validated SMILES set, one unambiguous target, and non-execution structure-lookup language. The planner emits fixed target-search, property, and ADMET steps; the configured Agent model remains outside this scientific plan and `gmm-llama` remains generator-only.

**Tech Stack:** FastAPI Web runtime, Python workflow planner, RDKit input validation, Pydantic Web requirements, pytest, real browser acceptance.

---

### Task 1: Add a focused composite-request classifier

**Files:**
- Create: `src/agent/planning/composite_request.py`
- Test: `tests/agent/test_composite_request.py`

- [ ] **Step 1: Write failing tests** for:
  - `请分析 CCO 的分子性质和 ADMET，并检索 PDE4A 的可用 PDB 结构` returning a single validated SMILES and target.
  - missing SMILES returning `None`.
  - two SMILES returning `None`.
  - two targets returning `None`.
  - explicit `运行对接` and positive generation returning `None`.
  - `推荐适合后续对接` remaining a lookup annotation and returning a candidate.

- [ ] **Step 2: Run**

```powershell
& 'C:\Users\xkx52\.conda\envs\MedChat\python.exe' -m pytest -q tests/agent/test_composite_request.py
```

Expected: collection or assertion failures because the classifier does not exist.

- [ ] **Step 3: Implement** `CompositeMoleculeTargetRequest` with:
  - `query`, `smiles`, `target`, and `reason` fields;
  - `parse_target_structure_molecule_request(query)` returning `None` for unsupported/ambiguous requests;
  - RDKit-backed whole-input SMILES parsing through existing molecular input utilities;
  - existing target request analysis and docking-intent helpers; no substring truncation.

- [ ] **Step 4: Run** the focused tests and expect all to pass.

### Task 2: Register deterministic workflow and planner steps

**Files:**
- Modify: `src/agent/workflows/catalog.py`
- Modify: `src/agent/planning/workflow_selection.py`
- Modify: `src/agent/planning/task_planner.py`
- Test: `tests/agent/test_planner_step_templates.py`
- Test: `tests/agent/test_composite_request.py`

- [ ] **Step 1: Add failing planner assertions** that the composite request produces exactly:

```text
target_database_search -> property_calculator -> admet_predictor
```

with target search bound to the validated target and both molecular tools bound to the validated SMILES. Assert no activity, generation, ranking, reverse-target, or docking step.

- [ ] **Step 2: Run** the focused planner tests and confirm failure.

- [ ] **Step 3: Add** `target_structure_molecule_analysis` to `WORKFLOW_POLICIES` with only `target_database_search`, `property_calculator`, and `admet_predictor`.

- [ ] **Step 4: Route** target-search queries with a valid composite request to the new workflow before the atomic target-search branch.

- [ ] **Step 5: Add** a deterministic `TaskPlanner` branch that creates three `WorkflowStep` objects with explicit input data, stable output keys, and ADMET marked optional/continue-on-error. Preserve the original query in context metadata for audit.

- [ ] **Step 6: Run** planner/template regression tests and the existing target-search matrix.

### Task 3: Open the Web admission boundary without weakening unsupported-task safety

**Files:**
- Modify: `src/web/decision_request.py`
- Modify: `src/web/decision_runtime.py`
- Modify: `src/web/scientific_obligations.py`
- Modify: `src/agent/scientific_session/web_requirements.py` only if the new workflow needs an explicit coverage exception.
- Test: `tests/agent/test_pi_full_web_entry.py`
- Test: `tests/agent/test_scientific_obligations.py`
- Test: `tests/agent/test_web_scientific_execution.py`

- [ ] **Step 1: Add failing admission tests** proving the composite request is classified as scientific, while generation, explicit docking execution, reverse-target, multiple targets, and missing SMILES remain rejected/clarified.

- [ ] **Step 2: Run** those tests and verify the current target-search-plus-molecule request still fails before the fix.

- [ ] **Step 3: Change** the early classifier to permit only the classifier’s validated composite candidate when target lookup is a suitability annotation; do not remove the general unsupported gate.

- [ ] **Step 4: Make** the property-only compiler opt out for the composite candidate so it cannot reject or replace the three-step plan.

- [ ] **Step 5: Rebuild** Web requirements using the new policy and run coverage validation; keep docking consent and prohibited-tool checks unchanged.

- [ ] **Step 6: Run** the full affected Web admission/execution tests.

### Task 4: Add partial-result presentation contract coverage

**Files:**
- Modify: `src/web/decision_chat.py` only if the composite output is not already rendered by existing projections.
- Test: `tests/agent/test_web_scientific_execution.py`
- Test: `tests/agent/test_optional_failure_summary.py`

- [ ] **Step 1: Add tests** asserting target, properties, and ADMET sections appear in stable order when all succeed.

- [ ] **Step 2: Add a fixture** where ADMET is unavailable and assert target/property observations remain visible, overall status is partial, and no fabricated ADMET values appear.

- [ ] **Step 3: Run** result projection tests and only change presentation code if the tests demonstrate a missing section.

### Task 5: Real frontend acceptance in the project environment

**Files:**
- No source changes unless a real acceptance failure identifies one.
- Record evidence in the task response; do not commit logs or outputs.

- [ ] **Step 1: Start** `main.py --port 6088 --no-reload` with `C:\Users\xkx52\.conda\envs\MedChat\python.exe`.

- [ ] **Step 2: In the browser**, confirm the header shows the configured Agent model and run the connection test without changing the model.

- [ ] **Step 3: Submit** `请分析 CCO 的分子性质和 ADMET，并检索 PDE4A 的可用 PDB 结构，推荐适合后续对接的结构。`

- [ ] **Step 4: Verify** visible steps are target lookup, properties, and ADMET; verify the result contains the real structure table, property values, ADMET summary, and the explicit pocket-evidence limitation.

- [ ] **Step 5: Submit** a negative control with two SMILES or an explicit docking execution request and verify it is clarified/rejected without scientific execution.

- [ ] **Step 6: Run** `compileall`, affected pytest suites, and `git diff --check`; report all services, tests, warnings, and unrun checks accurately.
