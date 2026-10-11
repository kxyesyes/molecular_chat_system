# Production Acceptance Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close locally reproducible acceptance defects without converting unavailable production evidence into a pass, then prepare a reviewable PR for the remaining target-host gates.

**Architecture:** Preserve the existing FastAPI, process-isolated scientific workers, SQLite task runtime, and fail-closed scientific result contracts. Fix only the activity worker's waiter-cleanup boundary, where a terminated child can raise after request cleanup and leave an unobserved asyncio task exception. Use focused regression tests followed by module acceptance and record external gates as unavailable until a real Linux deployment supplies evidence.

**Tech Stack:** Python 3.10+, FastAPI, asyncio, multiprocessing, pytest, Node.js static tests, PowerShell.

---

### Task 1: Reproduce and close activity waiter cleanup

**Files:**
- Modify: `tests/test_activity_route_budget.py`
- Modify: `src/web/routes/activity_prediction_routes.py`

- [x] **Step 1: Add a failing cleanup regression**

Add a test that passes an already-failed waiter to `_stop_activity_process()` and asserts cleanup consumes the `ProcessExecutionError` rather than propagating it or emitting an unobserved-task warning.

```python
def test_activity_cleanup_consumes_waiter_process_failure():
    async def exercise():
        async def fail_fast():
            from src.web.process_isolation import ProcessExecutionError
            raise ProcessExecutionError("child pipe closed")

        waiter = asyncio.create_task(fail_fast())
        await asyncio.sleep(0)
        await routes._stop_activity_process(
            type("StoppedProcess", (), {
                "terminate": lambda self: None,
                "close": lambda self: None,
            })(),
            waiter,
        )
        assert waiter.done()

    asyncio.run(exercise())
```

- [x] **Step 2: Run the test and verify RED**

Run:

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_activity_route_budget.py -q -p no:cacheprovider -k waiter_process_failure
```

Expected: the test fails because `_stop_activity_process()` does not consume a completed `ProcessExecutionError` waiter.

- [x] **Step 3: Implement the minimal cleanup contract**

In `_stop_activity_process()`, await the shielded waiter after termination so already-completed failures are retrieved as well as pending failures. Preserve the existing one-second timeout, cancellation cleanup, process close, and request-cancellation behavior; cleanup catches only the waiter/cleanup boundary failures.

- [x] **Step 4: Run the focused activity suite**

Run:

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_activity_route_budget.py tests/test_pharm3d_resource_budget.py -q -p no:cacheprovider -s
```

Observed: 37 tests passed with no `Task exception was never retrieved` output.

- [x] **Step 5: Commit the fix**

```powershell
git add src/web/routes/activity_prediction_routes.py tests/test_activity_route_budget.py
git commit -m "fix: consume activity worker waiter failures"
```

### Task 2: Run the functional regression matrix

**Files:** existing source and test files only; no source changes unless a new reproducible failure is found.

- [x] **Step 1: Run target-search and database checks**

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_target_search.py tests/test_target_db_validation.py tests/test_target_test_isolation.py -q -p no:cacheprovider
```

- [x] **Step 2: Run docking/scientific-quality checks**

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_docking_p0_correctness.py tests/test_docking_p1_scientific_integrity.py tests/test_docking_manifest_execution.py tests/test_stale_structure_docking_boundary.py tests/test_docking_interaction_analysis.py tests/test_docking_reproducibility.py tests/test_docking_batch_queue.py -q -p no:cacheprovider
```

- [x] **Step 3: Run ADMET, pharmacophore, activity, and task-runtime checks**

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_admet_routes.py tests/test_admet_page_routes.py tests/test_admet_unknown_evidence.py tests/test_reverse_target_pharmacophore.py tests/test_pharm3d_resource_budget.py tests/test_activity_prediction_reliability.py tests/test_activity_family_predictor.py tests/test_activity_route_budget.py tests/task_runtime -q -p no:cacheprovider
```

- [x] **Step 4: Run static checks**

```powershell
conda run --no-capture-output -n MedChat python -m compileall -q src scripts
node tests/home_agent_task_panel_test.js
node tests/reverse_target_broad_recall_test.js
node tests/reverse_target_pagination_test.js
```

For JavaScript changes, run `node --check` for every non-vendor, non-ketcher JavaScript file.

### Task 3: Record production-host evidence boundaries

**Files:** `docs/handoff/latest.md`

- [x] **Step 1: Run local strict health and contract acceptance**

```powershell
conda run --no-capture-output -n MedChat python scripts/health_check.py --strict
conda run --no-capture-output -n MedChat python scripts/run_agent_acceptance.py --mode contract
```

- [x] **Step 2: Record only observed evidence**

Record exact pass/skip counts and keep these gates unavailable unless a target Linux host proves them: real PDE/BuChE active bundles, external model endpoint, Vina/Meeko execution, ProLIF/MDAnalysis geometry, real reference-pose redocking RMSD, HTTPS/WSS proxying, multi-worker/restart recovery, Temporal production worker, and cancellation/cleanup under the deployed reverse proxy.

- [x] **Step 3: Review and commit scoped changes**

```powershell
git diff --check
git status --short
```

Stage only the plan, source/test fix, and handoff evidence. Never stage the pre-existing `data/molecular_faiss_index.index.manifest.json`.

### Task 4: Independent review and PR integration

- [x] **Step 1: Request review against `origin/main`**
- [x] **Step 2: Resolve all Important/Critical findings**
- [ ] **Step 3: Push the branch and create a draft PR targeting `main`**
- [ ] **Step 4: After checks pass, mark ready and squash-merge only the scoped PR**
- [ ] **Step 5: Fast-forward local `main` and verify the merge commit**

## Acceptance boundary

Local green tests do not satisfy the requested production gates. The goal remains active until the target server supplies real model, scientific-tool, proxy, restart, and deployment evidence; no unavailable result may be reported as a scientific or production pass.
