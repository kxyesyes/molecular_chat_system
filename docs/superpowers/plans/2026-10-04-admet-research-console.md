# ADMET Research Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static KERMT ADMET demo with a real, evidence-preserving ADMET research console at `/admet`, while removing `/kermt-admet` completely.

**Architecture:** Add a thin `POST /api/admet/predict` adapter that invokes the existing `ADMETPredictor` in a worker thread and returns its structured result unchanged. Keep all scientific validation, model availability checks, provenance, warnings, and partial/failure states in the existing tool. Build the UI as a Jinja template plus focused CSS and native JavaScript that renders only response data and never invents values.

**Tech Stack:** FastAPI, Jinja2, existing `ADMETPredictor`, Starlette threadpool helper, native JavaScript, CSS, pytest, Node.js static tests.

---

## File map

Create:

- `src/web/routes/admet_routes.py` — thin HTTP adapter for the existing ADMET tool.
- `src/web/templates/admet.html` — new semantic page shell.
- `src/web/static/css/admet.css` — scoped research-console visual system.
- `src/web/static/js/admet.js` — request state machine and safe result rendering.
- `tests/test_admet_routes.py` — route contract tests with injected predictor.
- `tests/test_admet_page_routes.py` — `/admet` and removed-route tests.
- `tests/frontend_admet_page_test.js` — static JS/template safety and no-hardcoded-result tests.

Modify:

- `src/web/routes/api_routes.py` — register `setup_admet_routes`.
- `src/web/routes/main_routes.py` — register `/admet`; remove `/kermt-admet`.
- `src/web/routes/page_routes.py` — replace the old path in `_MAIN_PAGE_PATHS`.
- `src/web/templates/index.html` — update both ADMET navigation links to `/admet`.

Do not modify or stage:

- `src/web/templates/kermt_admet.html` — leave as an unused legacy asset in this task.
- `data/molecular_faiss_index.index.manifest.json` — existing untracked user/worktree asset.
- ADMET model code, model weights, Agent planner/router, or scientific thresholds.

### Task 1: Lock the API and route behavior with failing tests

**Files:**

- Create: `tests/test_admet_routes.py`
- Create: `tests/test_admet_page_routes.py`

- [ ] **Step 1: Write the injected predictor fixture and API contract tests.**

Use an injected fake predictor only as a transport fixture; the tests must assert that the route preserves the real tool envelope instead of calculating or rewriting science values:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.routes.admet_routes import setup_admet_routes


class FakePredictor:
    def __init__(self, result):
        self.result = result
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return self.result

    def close(self):
        return None


def make_client(monkeypatch, result):
    predictor = FakePredictor(result)
    monkeypatch.setattr(
        "src.web.routes.admet_routes.build_admet_predictor",
        lambda: predictor,
    )
    app = FastAPI()
    setup_admet_routes(app)
    return TestClient(app), predictor


def test_predict_returns_success_envelope_and_preserves_provenance(monkeypatch):
    expected = {
        "success": True,
        "status": "succeeded",
        "message": "real result",
        "data": [{"molecule_id": "molecule-001", "status": "succeeded"}],
        "warnings": ["model prediction is not an experiment"],
        "quality": {"backend": "admet_ai", "model_version": "1.4.0"},
        "provenance": {
            "tool_name": "admet_predictor",
            "model_name": "ADMET-AI",
            "demo_mode": False,
            "fallback_used": False,
        },
    }
    client, predictor = make_client(monkeypatch, expected)

    response = client.post(
        "/api/admet/predict",
        json={"smiles": "CCO", "molecule_id": "molecule-001"},
    )

    assert response.status_code == 200
    assert response.json() == expected
    assert predictor.queries == [
        {"smiles": ["CCO"], "molecule_ids": ["molecule-001"]}
    ]


def test_predict_preserves_unavailable_and_failed_states(monkeypatch):
    expected = {
        "success": False,
        "status": "unavailable",
        "message": "ADMET-AI unavailable",
        "data": None,
        "warnings": [],
    }
    client, _ = make_client(monkeypatch, expected)

    response = client.post("/api/admet/predict", json={"smiles": "CCO"})

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["success"] is False


def test_predict_rejects_missing_or_oversized_input(monkeypatch):
    client, _ = make_client(monkeypatch, {"success": True})

    assert client.post("/api/admet/predict", json={}).status_code == 422
    assert client.post("/api/admet/predict", json={"smiles": "x" * 8193}).status_code == 422


def test_predict_does_not_call_predictor_for_invalid_http_payload(monkeypatch):
    client, predictor = make_client(monkeypatch, {"success": True})

    response = client.post("/api/admet/predict", json={"smiles": 42})

    assert response.status_code == 422
    assert predictor.queries == []
```

- [ ] **Step 2: Run the new API tests and confirm they fail because the adapter does not exist.**

Run:

```powershell
python -m pytest tests/test_admet_routes.py -q -p no:cacheprovider
```

Expected: collection/import failure for missing `src.web.routes.admet_routes`.

- [ ] **Step 3: Write the page route tests.**

The tests must construct the existing Jinja route registration and assert that the new page renders and the old path is not registered:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from src.web.routes.main_routes import register_main_routes


def test_admet_page_is_the_only_admet_entrypoint():
    app = FastAPI()
    templates = Jinja2Templates(directory="src/web/templates")
    register_main_routes(app, templates)
    paths = {route.path for route in app.routes}

    assert "/admet" in paths
    assert "/kermt-admet" not in paths

    with TestClient(app) as client:
        response = client.get("/admet")
    assert response.status_code == 200
    assert "ADMET 研究控制台" in response.text


def test_page_routes_inventory_has_no_removed_path():
    from src.web.routes.page_routes import _MAIN_PAGE_PATHS

    assert "/admet" in _MAIN_PAGE_PATHS
    assert "/kermt-admet" not in _MAIN_PAGE_PATHS
```

- [ ] **Step 4: Run the page tests and confirm they fail before implementation.**

Run:

```powershell
python -m pytest tests/test_admet_page_routes.py -q -p no:cacheprovider
```

Expected: `/admet` is absent and the new template text is absent.

### Task 2: Implement the thin real-tool HTTP adapter

**Files:**

- Create: `src/web/routes/admet_routes.py`
- Modify: `src/web/routes/api_routes.py`

- [ ] **Step 1: Add the adapter with strict transport validation.**

Implement these functions and behavior:

```python
from typing import Any

from fastapi import Body, HTTPException
from starlette.concurrency import run_in_threadpool

from src.agent.tools.admet_predictor import ADMETPredictor


def build_admet_predictor() -> ADMETPredictor:
    return ADMETPredictor()


def setup_admet_routes(app):
    @app.post("/api/admet/predict")
    async def predict_admet(payload: dict[str, Any] = Body(...)):
        if not isinstance(payload, dict):
            raise HTTPException(status_code=422, detail="请求体必须是对象")
        smiles = payload.get("smiles")
        if not isinstance(smiles, str) or not smiles.strip() or len(smiles) > 8192:
            raise HTTPException(status_code=422, detail="smiles 必须是 1-8192 个字符的非空字符串")
        molecule_id = payload.get("molecule_id", "molecule-001")
        if not isinstance(molecule_id, str) or not molecule_id.strip() or len(molecule_id) > 128:
            raise HTTPException(status_code=422, detail="molecule_id 无效")

        predictor = build_admet_predictor()
        try:
            return await run_in_threadpool(
                predictor.execute,
                {"smiles": [smiles.strip()], "molecule_ids": [molecule_id.strip()]},
            )
        finally:
            close = getattr(predictor, "close", None)
            if callable(close):
                close()
```

The adapter must not turn tool `success=False`, `status=unavailable`, or `status=failed` into HTTP errors. Scientific failure is a valid structured result and must reach the page. Only malformed transport input gets HTTP 422. Exceptions from the tool should be logged by the existing application boundary and returned as a non-success structured failure without exposing secrets or tracebacks.

- [ ] **Step 2: Register the adapter exactly once in `setup_api_routes`.**

Import `setup_admet_routes` beside the other route registrars and call it once after the molecule-properties registration. Do not add an alias route.

- [ ] **Step 3: Run the API tests and verify the adapter passes.**

Run:

```powershell
python -m pytest tests/test_admet_routes.py -q -p no:cacheprovider
```

Expected: all new API tests pass.

- [ ] **Step 4: Commit the transport layer.**

```powershell
git add src/web/routes/admet_routes.py src/web/routes/api_routes.py tests/test_admet_routes.py
git commit -m "feat: expose structured ADMET prediction endpoint"
```

### Task 3: Replace the page route and navigation entrypoints

**Files:**

- Modify: `src/web/routes/main_routes.py`
- Modify: `src/web/routes/page_routes.py`
- Modify: `src/web/templates/index.html`
- Create: `src/web/templates/admet.html`

- [ ] **Step 1: Change the page route and route inventory.**

Register:

```python
@app.get("/admet")
async def admet_page(request: Request):
    if templates is not None:
        return render_template(request, "admet.html")
    return {"message": "ADMET Research Console", "status": "running"}
```

Delete only the `/kermt-admet` route function and change `_MAIN_PAGE_PATHS` to contain `/admet`. Do not add a redirect or compatibility handler.

- [ ] **Step 2: Update both homepage navigation links.**

Replace only the two exact `window.location.href='/kermt-admet'` references in `src/web/templates/index.html` with `/admet`. Do not reformat the large homepage template.

- [ ] **Step 3: Create a data-only semantic page shell.**

The template must contain:

- title and page header;
- form `id="admet-form"`, input `id="smiles-input"`, example button `id="load-example"`, submit `id="run-admet"`, reset `id="reset-admet"`;
- live status region `id="run-status"` with `aria-live="polite"`;
- overview region `id="admet-overview"`;
- endpoint container `id="endpoint-groups"`;
- evidence container `id="evidence-panel"`;
- empty/error containers with stable IDs;
- `<link rel="stylesheet" href="/static/css/admet.css">` and `<script defer src="/static/js/admet.js"></script>`.

No numerical ADMET values, “safe” labels, or mock results may appear in the HTML source. All result text must be inserted by `admet.js` from the response.

- [ ] **Step 4: Run the page tests and verify the old path is absent.**

Run:

```powershell
python -m pytest tests/test_admet_page_routes.py -q -p no:cacheprovider
```

Expected: all page route tests pass.

- [ ] **Step 5: Commit the route and template migration.**

```powershell
git add src/web/routes/main_routes.py src/web/routes/page_routes.py src/web/templates/index.html src/web/templates/admet.html tests/test_admet_page_routes.py
git commit -m "feat: replace legacy ADMET page entrypoint"
```

### Task 4: Build the research-console visual layer

**Files:**

- Create: `src/web/static/css/admet.css`

- [ ] **Step 1: Add the graphite/teal/amber design tokens and responsive layout.**

Define CSS custom properties for background, panel, border, text, muted, verified, warning, failure, and uncomputed states. Implement the three-column desktop grid and one-column mobile layout. Include focus-visible styles, `prefers-reduced-motion`, and a readable minimum contrast. Scope selectors under `.admet-page` so this page cannot leak styles into the homepage.

- [ ] **Step 2: Style explicit scientific states.**

Provide classes for `.state-success`, `.state-partial`, `.state-unavailable`, `.state-failed`, and `.state-not-calculated`. A missing endpoint must look unavailable, not like a zero or a green pass.

- [ ] **Step 3: Run a CSS/source sanity check.**

Run:

```powershell
rg -n "-4\.65|0\.78|0\.12|0\.65|0\.15|0\.02|mock|random|setTimeout" src/web/templates/admet.html src/web/static/css/admet.css
```

Expected: no output.

### Task 5: Implement safe, provenance-preserving frontend rendering

**Files:**

- Create: `src/web/static/js/admet.js`
- Create: `tests/frontend_admet_page_test.js`

- [ ] **Step 1: Write static frontend tests before implementing the renderer.**

The Node test must assert:

```javascript
const source = read("src/web/static/js/admet.js");
const template = read("src/web/templates/admet.html");

assert(!source.includes("innerHTML"), "ADMET renderer must not use innerHTML");
assert(!source.includes("onclick="), "ADMET renderer must not build inline handlers");
assert(!template.includes("/kermt-admet"), "new page must not preserve removed route");
for (const value of ["-4.65", "0.78", "0.12", "0.65", "0.15", "0.02"]) {
  assert(!template.includes(value), `template contains hardcoded ADMET value ${value}`);
  assert(!source.includes(value), `renderer contains hardcoded ADMET value ${value}`);
}
for (const required of [
  "prediction_method", "demo_mode", "fallback_used", "provenance",
  "warnings", "unavailable", "partial", "not_calculated",
]) {
  assert(source.includes(required), `renderer missing ${required} state/evidence handling`);
}
```

- [ ] **Step 2: Run the static test and confirm it fails because the JS file is absent.**

Run:

```powershell
node tests/frontend_admet_page_test.js
```

Expected: missing file failure.

- [ ] **Step 3: Implement the renderer with DOM APIs only.**

Implement a small module with these functions:

```javascript
const ENDPOINT_GROUPS = [
  { key: "physicochemical", title: "物化性质" },
  { key: "absorption", title: "吸收" },
  { key: "distribution", title: "分布" },
  { key: "metabolism", title: "代谢" },
  { key: "excretion", title: "排泄" },
  { key: "toxicity", title: "毒性" },
  { key: "druglikeness", title: "药物相似性" },
];

function setText(element, value) {
  element.textContent = value == null || value === "" ? "未提供" : String(value);
}

function renderResponse(response) {
  // Clear and repopulate only with createElement/textContent.
  // Preserve response.status, data, warnings, quality, provenance and reasoning.
}
```

The implementation must:

- use `fetch("/api/admet/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(...) })`;
- disable submit while running and restore it in `finally`;
- display the server `status` and `message` without converting failure to success;
- render each row from `data`, preserving `molecule_id`, row status, row error, row warnings and row `admet` fields;
- enumerate nested endpoint groups using `Object.entries` and `textContent`, with no scientific assumptions about field names beyond display labels;
- show `prediction_method`, `backend_version`, `model_name`, `model_version`, `weights_id`, `demo_mode`, `fallback_used`, `warnings`, `reasoning`, and `provenance` when supplied;
- explicitly label missing values as `未计算` or `未提供`;
- show a red/amber evidence warning when `demo_mode !== false` or `fallback_used !== false`;
- keep an invalid-input response from rendering any endpoint cards;
- treat HTTP errors as transport failures with a human-readable error state, not as scientific success;
- use event listeners for all controls and no inline `onclick` or raw HTML interpolation.

- [ ] **Step 4: Run the static frontend tests and syntax check.**

Run:

```powershell
node tests/frontend_admet_page_test.js
node --check src/web/static/js/admet.js
```

Expected: both commands pass.

- [ ] **Step 5: Commit the UI layer.**

```powershell
git add src/web/static/css/admet.css src/web/static/js/admet.js tests/frontend_admet_page_test.js
git commit -m "feat: add evidence-first ADMET console UI"
```

### Task 6: Integrate and run focused regression checks

**Files:**

- No new production files; only fix defects found in the focused checks within the files above.

- [ ] **Step 1: Run focused Python tests.**

```powershell
python -m pytest tests/test_admet_routes.py tests/test_admet_page_routes.py tests/test_molecule_properties_unknown.py -q -p no:cacheprovider
```

Expected: all focused tests pass; the existing `/api/molecule/properties` contract remains unchanged.

- [ ] **Step 2: Run focused Node tests.**

```powershell
node tests/frontend_admet_page_test.js
node tests/home_agent_task_panel_test.js
node tests/frontend_safe_render_test.js
node --check src/web/static/js/admet.js
```

Expected: all commands pass.

- [ ] **Step 3: Run source compilation and relevant page/API regression.**

```powershell
python -m compileall -q src scripts
python -m pytest tests/test_main_routes_template_compat.py tests/test_api_route_boundary.py -q -p no:cacheprovider
```

Expected: pass, or document an environment-specific dependency failure without changing scientific behavior.

- [ ] **Step 4: Verify route inventory and worktree scope.**

```powershell
rg -n "/kermt-admet|window\.location\.href=.*kermt|kermt_admet" src/web/routes src/web/templates/index.html src/web/templates/admet.html src/web/static/js/admet.js
git diff --check
git status --short --branch
```

Expected: no registered/link references to `/kermt-admet`; the only remaining legacy reference may be the intentionally unused `kermt_admet.html` filename if it is inspected outside this command. The manifest remains untracked and unstaged.

- [ ] **Step 5: Commit only task files and report.**

Before the final commit, use `git diff --name-only main...HEAD` and verify only the planned files and commits are included. Do not stage with `git add -A`; do not stage `data/molecular_faiss_index.index.manifest.json`.

```powershell
git status --short
git log --oneline --decorate -6
```

If all checks pass, create the final integration commit:

```powershell
git add src/web/routes/main_routes.py src/web/routes/page_routes.py src/web/routes/api_routes.py src/web/routes/admet_routes.py src/web/templates/index.html src/web/templates/admet.html src/web/static/css/admet.css src/web/static/js/admet.js tests/test_admet_routes.py tests/test_admet_page_routes.py tests/frontend_admet_page_test.js
git commit -m "feat: redesign ADMET research console"
```

No merge into `main` is included in this plan.

## Self-review against the design spec

- Goal and no-hardcoded-results requirement: Tasks 3–5.
- `/admet` only and no compatibility route: Tasks 1 and 3, verified in Task 6.
- Reuse existing ADMET predictor and preserve provenance: Task 2 and Task 5.
- Real success/unavailable/partial/failed/invalid states: Tasks 1, 2, and 5.
- Responsive visual system and accessibility: Task 4.
- Safe rendering: Task 5 static test and DOM-only renderer.
- Regression coverage and no unrelated changes: Task 6.
- No scientific model or planner rewrite: File map and non-goal boundaries.

No unresolved design placeholders remain. The only implementation decision is now fixed: because no equivalent real ADMET HTTP endpoint exists, add the thin `/api/admet/predict` adapter described in Task 2.
