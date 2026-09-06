# Remove Admin Token Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove MedChat's administrator-token prompt, browser token helper, backend token dependency, environment setting, and test fixtures without changing unrelated management behavior.

**Architecture:** Management endpoints become ordinary same-origin FastAPI routes. Browser callers use native `fetch`; API Key persistence and response masking remain unchanged. Existing route tests are converted from authentication assertions to functional no-token assertions, and a repository-level static check prevents the prompt or authentication symbols from returning.

**Tech Stack:** Python 3.10+, FastAPI, pytest, vanilla JavaScript, Node.js static tests.

---

### Task 1: Establish the removal contract

**Files:**
- Modify: `tests/test_admin_auth_routes.py`
- Modify: `tests/admin_fetch_test.js`

- [ ] **Step 1: Replace authentication-gate assertions with no-token access assertions**

In `tests/test_admin_auth_routes.py`, remove `ADMIN_TOKEN`, `admin_headers()`, and assertions expecting `401`/`403`. Add a focused test that constructs the existing task/workflow/system routes and calls representative endpoints without authentication headers:

```python
def test_management_routes_do_not_require_admin_token(tmp_path, monkeypatch):
    monkeypatch.setattr(task_manager_module, "DB_PATH", tmp_path / "tasks.sqlite3")
    app = FastAPI()
    setup_task_routes(app)
    setup_agent_workflow_routes(app, MockWorkflowService())
    setup_system_routes(app)
    client = TestClient(app)

    assert client.get("/api/tasks").status_code == 200
    assert client.post("/api/agent/workflows/plan", json={"query": "analyze CCO"}).status_code == 200
    assert client.get("/api/system/runtime").status_code == 200
```

Keep the existing LLM persistence, path masking, docking history, and activity-model functional assertions, but call the routes without `admin_headers()` and without setting `MEDCHAT_ADMIN_TOKEN`.

- [ ] **Step 2: Convert the Node test into a no-auth static contract**

Keep the filename `tests/admin_fetch_test.js` so existing commands remain compatible. Remove the simulated token/session storage setup and assert instead:

```javascript
assert.doesNotMatch(allRuntimeSource, /MedChatAdminAuth|medchat_admin_token|X-MedChat-Admin-Token/);
assert.doesNotMatch(allRuntimeSource, /prompt\s*\(/);
assert.doesNotMatch(allTemplates, /shared\/admin_fetch\.js/);
assert.match(homeSource, /fetch\("\/api\/llm\/config"/);
```

Read the affected runtime JavaScript and templates explicitly so the test covers home, docking, activity model management, and training monitoring.

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_admin_auth_routes.py -q -p no:cacheprovider
node tests\admin_fetch_test.js
```

Expected: failures because route dependencies, prompt code, helper references, and protected response statuses still exist.

### Task 2: Remove backend token enforcement

**Files:**
- Delete: `src/web/admin_auth.py`
- Modify: `src/web/app.py`
- Modify: `src/task_runtime/routes.py`
- Modify: `src/web/routes/agent_workflow_routes.py`
- Modify: `src/web/routes/api_routes.py`
- Modify: `src/web/routes/system_routes.py`
- Modify: `.env.example`

- [ ] **Step 1: Remove authentication imports and dependencies**

Delete every `from ...admin_auth import require_admin` import and remove `dependencies=[Depends(require_admin)]` from the affected route decorators. Where `Depends` is no longer used in a module, remove it from the FastAPI import. Route paths, HTTP methods, payload parsing, and response shapes must remain unchanged.

- [ ] **Step 2: Delete the authentication module and environment placeholder**

Delete `src/web/admin_auth.py`. Remove this line from `.env.example`:

```dotenv
MEDCHAT_ADMIN_TOKEN=
```

Do not modify the API Key placeholders or local `.env` persistence settings.

- [ ] **Step 3: Run the Python removal contract**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_admin_auth_routes.py -q -p no:cacheprovider
```

Expected: route tests pass without token headers; any remaining failure must identify a missed dependency or obsolete test fixture.

### Task 3: Remove the browser prompt and helper

**Files:**
- Delete: `src/web/static/js/shared/admin_fetch.js`
- Modify: `src/web/static/js/home/main.js`
- Modify: `src/web/static/js/docking/ui_manager.js`
- Modify: `src/web/static/js/activity_prediction/model_manager.js`
- Modify: `src/web/static/js/activity_prediction/training_monitor.js`
- Modify: `src/web/static/js/activity_prediction/main.js`
- Modify: `src/web/templates/index.html`
- Modify: `src/web/templates/molecular_docking.html`
- Modify: `src/web/templates/activity_prediction.html`
- Test: `tests/admin_fetch_test.js`

- [ ] **Step 1: Replace helper calls with native fetch**

Apply these behavior-preserving substitutions:

```javascript
window.MedChatAdminAuth.fetch(url, options) -> fetch(url, options)
Admin.fetch(url, options) -> fetch(url, options)
```

Remove `const Admin = window.MedChatAdminAuth` and its availability guard from docking and activity model modules. Preserve request URLs, methods, headers other than the admin header, JSON bodies, and error handling.

- [ ] **Step 2: Remove helper script tags and file**

Delete each template reference:

```html
<script src="/static/js/shared/admin_fetch.js"></script>
```

Then delete `src/web/static/js/shared/admin_fetch.js`. Do not add another prompt, token store, login dialog, Cookie, or replacement authentication helper.

- [ ] **Step 3: Run frontend removal and safety checks**

Run:

```powershell
node tests\admin_fetch_test.js
node --check src\web\static\js\home\main.js
node --check src\web\static\js\docking\ui_manager.js
node --check src\web\static\js\activity_prediction\model_manager.js
node --check src\web\static\js\activity_prediction\training_monitor.js
node --check src\web\static\js\activity_prediction\main.js
node tests\frontend_safe_render_test.js
```

Expected: all checks pass and no runtime source contains `prompt(` or `MedChatAdminAuth`.

### Task 4: Remove obsolete token fixtures from the test suite

**Files:**
- Modify: `tests/test_temporal_docking_routes.py`
- Modify: `tests/test_docking_history_index.py`
- Modify: `tests/test_phase2_phase3_routes.py`
- Modify: `tests/test_docking_agent_architecture.py`
- Modify: `tests/test_activity_prediction_contract.py`
- Modify: `tests/test_activity_model_registry.py`

- [ ] **Step 1: Remove token environment setup and headers**

Delete `monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ...)`, token constants, and `X-MedChat-Admin-Token` header construction. Preserve unrelated headers such as `If-Match`, idempotency keys, content type, and request bodies. Route status and payload assertions remain unchanged unless they asserted authentication behavior.

- [ ] **Step 2: Verify no effective authentication references remain**

Run:

```powershell
rg -n "MEDCHAT_ADMIN_TOKEN|X-MedChat-Admin-Token|require_admin|MedChatAdminAuth|admin_fetch\.js|管理员令牌" src tests .env.example
```

Expected: no matches. Historical design documents are excluded because they describe prior architecture and are not executable code.

- [ ] **Step 3: Run affected route suites**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_admin_auth_routes.py tests\test_temporal_docking_routes.py tests\test_docking_history_index.py tests\test_phase2_phase3_routes.py tests\test_docking_agent_architecture.py tests\test_activity_prediction_contract.py tests\test_activity_model_registry.py -q -p no:cacheprovider
```

Expected: all tests pass without authentication fixtures.

### Task 5: Full regression, review, commit, and live verification

**Files:**
- Modify: `docs/superpowers/plans/2026-09-02-remove-admin-token-auth.md` (check completed steps)

- [ ] **Step 1: Run full relevant regression**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py tests\test_openai_compatible_model.py tests\test_llm_runtime_config.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
node tests\admin_fetch_test.js
node tests\frontend_safe_render_test.js
node tests\home_agent_task_panel_test.js
```

Expected: no failures. Existing optional-dependency skips and FastAPI lifecycle deprecation warnings may remain and must be reported accurately.

- [ ] **Step 2: Request independent review**

Ask a review agent to inspect the final diff for missed auth references, accidentally exposed API Keys, changed route semantics, and unrelated edits. Resolve every Critical or Important finding before commit.

- [ ] **Step 3: Stage exact files and commit**

Use explicit paths only; never stage the existing untracked FAISS manifest or local `.env`:

```powershell
git add -- .env.example src/web/app.py src/task_runtime/routes.py src/web/routes/agent_workflow_routes.py src/web/routes/api_routes.py src/web/routes/system_routes.py src/web/static/js/home/main.js src/web/static/js/docking/ui_manager.js src/web/static/js/activity_prediction/model_manager.js src/web/static/js/activity_prediction/training_monitor.js src/web/static/js/activity_prediction/main.js src/web/templates/index.html src/web/templates/molecular_docking.html src/web/templates/activity_prediction.html tests/admin_fetch_test.js tests/test_admin_auth_routes.py tests/test_temporal_docking_routes.py tests/test_docking_history_index.py tests/test_phase2_phase3_routes.py tests/test_docking_agent_architecture.py tests/test_activity_prediction_contract.py tests/test_activity_model_registry.py docs/superpowers/plans/2026-09-02-remove-admin-token-auth.md
git rm -- src/web/admin_auth.py src/web/static/js/shared/admin_fetch.js
git commit -m "refactor: remove admin token authentication"
```

- [ ] **Step 4: Restart and verify without a token**

Restart `python main.py --no-reload`, then call `/api/llm/config`, `/api/tasks`, and `/api/system/runtime` without authentication headers. Verify successful business responses, `api_key_configured: true`, and absence of an `api_key` field. Load `/` and verify `admin_fetch.js`, `MedChatAdminAuth`, and the administrator-token prompt text are absent.
