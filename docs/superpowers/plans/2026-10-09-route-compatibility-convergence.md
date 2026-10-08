# Route Compatibility Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the four remaining scientific route registration paths from their legacy `_support` fallback while preserving the public compatibility entry points.

**Architecture:** Each affected module will expose a production registration function that accepts only narrow dependencies. The existing `setup_*_routes` function remains as a compatibility wrapper and resolves `_support` lazily only for old direct callers. `api_routes.setup_api_routes` will call the production registration functions directly, so the application path cannot accidentally depend on the facade.

**Tech Stack:** FastAPI, Python 3.10+, pytest, existing route compatibility helper.

---

### Task 1: Establish the production-registration boundary contract

**Files:**
- Modify: `tests/test_route_compatibility.py`
- Modify: `tests/test_api_route_boundary.py`

- [ ] **Step 1: Write the failing tests**

Add assertions that `activity_model_routes`, `activity_prediction_routes`, `molecule_utility_routes`, and `docking_report_routes` export `register_*_routes` functions whose signatures do not contain `_support`, and that `setup_api_routes` invokes those production functions with narrow dependencies.

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
python -m pytest tests/test_route_compatibility.py tests/test_api_route_boundary.py -q
```

Expected result: the new boundary assertions fail because the production registration functions do not exist yet.

### Task 2: Split the four production registration functions from compatibility wrappers

**Files:**
- Modify: `src/web/routes/activity_model_routes.py`
- Modify: `src/web/routes/activity_prediction_routes.py`
- Modify: `src/web/routes/molecule_utility_routes.py`
- Modify: `src/web/routes/docking_report_routes.py`

- [ ] **Step 1: Implement the narrow registration functions**

Move the existing endpoint closure bodies into `register_activity_model_routes`, `register_activity_prediction_routes`, `register_molecule_utility_routes`, and `register_docking_report_routes`. These functions accept only the concrete dependency providers they use and contain no `_support` parameter or lookup.

- [ ] **Step 2: Keep compatibility wrappers**

Keep each existing `setup_*_routes` public entry point. It resolves legacy attributes through `lazy_dependency` and delegates to the corresponding `register_*_routes`; no endpoint closure reads `_support` directly.

- [ ] **Step 3: Run the focused tests**

Run:

```powershell
python -m pytest tests/test_route_compatibility.py tests/test_api_route_boundary.py -q
```

Expected result: the new boundary assertions pass and the existing legacy monkeypatch tests remain green.

### Task 3: Wire production application registration to narrow functions

**Files:**
- Modify: `src/web/routes/api_routes.py`
- Modify: `tests/test_api_route_boundary.py`

- [ ] **Step 1: Update the application facade**

Import the four `register_*_routes` functions and call them from `setup_api_routes`. Retain the old `setup_*_routes` names for direct callers and tests, but do not use them on the formal application path.

- [ ] **Step 2: Verify explicit dependency identity**

Update the facade test to capture production registration calls and assert that every dependency is the narrow object already constructed by `api_routes`, with no `_support` keyword.

- [ ] **Step 3: Run route and compile checks**

Run:

```powershell
python -m pytest tests/test_route_compatibility.py tests/test_api_route_boundary.py tests/test_web_route_modules.py -q
python -m compileall -q src/web/routes
```

Expected result: all focused tests pass and compilation exits with status 0.

### Task 4: Audit and document the compatibility boundary

**Files:**
- Modify: `docs/AGENT_MAINTENANCE.md`
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Record the boundary**

Document that `_support` is retained only by the four compatibility wrappers, while production registration uses `register_*_routes` and narrow providers. Record that public direct-call compatibility and dynamic monkeypatch behavior are intentionally preserved.

- [ ] **Step 2: Run the complete relevant checks**

Run:

```powershell
python -m pytest tests/test_route_compatibility.py tests/test_api_route_boundary.py -q
python -m compileall -q src scripts
git diff --check
```

Expected result: all tests pass, compilation succeeds, and `git diff --check` prints nothing.

- [ ] **Step 3: Commit the isolated change**

```powershell
git add src/web/routes/activity_model_routes.py src/web/routes/activity_prediction_routes.py src/web/routes/molecule_utility_routes.py src/web/routes/docking_report_routes.py src/web/routes/api_routes.py tests/test_route_compatibility.py tests/test_api_route_boundary.py docs/AGENT_MAINTENANCE.md docs/handoff/latest.md docs/superpowers/plans/2026-10-09-route-compatibility-convergence.md
git commit -m "refactor: isolate production route dependencies"
```
