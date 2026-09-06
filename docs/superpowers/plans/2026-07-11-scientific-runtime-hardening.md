# MedChat Scientific Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MedChat's browser, Agent, RAG, activity, docking, and task runtimes safe, request-isolated, scientifically explicit, and reproducibly tested.

**Architecture:** Preserve FastAPI, Jinja2, native JavaScript, RDKit, local Ollama, RG-MPNN, Vina, and SQLite. Introduce narrow compatibility helpers and structured provenance at existing boundaries; keep every stage independently green and commit each behavior separately.

**Tech Stack:** Python 3.10, FastAPI, Pydantic 2, SQLite, pytest, native JavaScript, Node static tests, RDKit, PyTorch/PyG, FAISS, Ollama, AutoDock Vina.

---

## File structure and ownership

- `src/web/static/js/shared/safe_render.js`: the only shared escaping/DOM-safe rendering primitives.
- `src/web/static/js/shared/admin_fetch.js`: the only browser helper for protected management APIs.
- `src/task_runtime/manager.py`: transport-level background-task state normalization.
- `src/agent/runtime/workflow_executor.py`: request-scoped orchestration boundary.
- `src/agent/tooling/factory.py` and `src/agent/specialists/agents.py`: delegated tool ownership.
- `src/docking/molecular_docking_service.py`: per-job docking filesystem and subprocess execution.
- `src/docking/history_index.py`: serialized atomic docking-history index access.
- `src/web/rag_index.py`: RAG manifest, source hash, and vector-to-row mapping.
- `src/web/app.py`: active application wiring only; RAG implementation delegates to `rag_index.py`.
- `src/activity/model_registry.py`: validated checkpoint metadata and confined model selection.
- `src/activity/predictor.py`: task-aware regression/classification inference.
- `src/activity/trainer.py`: split propagation and reproducible metadata.
- `src/agent/evaluation/scientific.py`: evidence-based dataflow and provenance scoring.
- `.github/workflows/quality.yml`: dependency-light contract and static checks.

## Stage 1 — Browser safety and task truthfulness

### Task 1: Add activity-page XSS contract tests

**Files:**
- Create: `tests/activity_prediction_safe_render_test.js`
- Modify: `tests/frontend_safe_render_test.js`
- Test: `tests/activity_prediction_safe_render_test.js`

- [ ] **Step 1: Write the failing activity rendering test**

Create a Node static test that loads the activity template and modules, requires
`/static/js/shared/safe_render.js` before activity modules, and rejects raw runtime
interpolation. The core assertions are:

```javascript
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

const template = read("src/web/templates/activity_prediction.html");
assert(
  template.indexOf("/static/js/shared/safe_render.js") <
    template.indexOf("/static/js/activity_prediction/model_manager.js"),
);

for (const file of ["model_manager.js", "preflight.js", "results_renderer.js"]) {
  const source = read(`src/web/static/js/activity_prediction/${file}`);
  assert(!source.includes("${model.name}"));
  assert(!source.includes("${model.target}"));
  assert(!source.includes("<th>${header}</th>"));
  assert(!source.includes("<td>${cell}</td>"));
}
```

Extend `frontend_safe_render_test.js` to inspect `home/chat_renderer.js` and reject
raw `${message}` in toast and notification `innerHTML` assignments.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
node tests/activity_prediction_safe_render_test.js
node tests/frontend_safe_render_test.js
```

Expected: both commands fail on the currently unescaped activity/chat sinks.

- [ ] **Step 3: Commit the failing tests**

```powershell
git add -- tests/activity_prediction_safe_render_test.js tests/frontend_safe_render_test.js
git commit -m "test(web): expose activity and chat rendering sinks"
```

### Task 2: Implement safe activity and chat rendering

**Files:**
- Modify: `src/web/templates/activity_prediction.html`
- Modify: `src/web/static/js/activity_prediction/model_manager.js`
- Modify: `src/web/static/js/activity_prediction/preflight.js`
- Modify: `src/web/static/js/activity_prediction/results_renderer.js`
- Modify: `src/web/static/js/activity_prediction/training_monitor.js`
- Modify: `src/web/static/js/home/chat_renderer.js`
- Test: `tests/activity_prediction_safe_render_test.js`
- Test: `tests/frontend_safe_render_test.js`

- [ ] **Step 1: Load the shared safety helper before activity modules**

Insert before `admin_fetch.js`:

```html
<script src="/static/js/shared/safe_render.js"></script>
```

- [ ] **Step 2: Replace untrusted HTML interpolation with DOM construction**

Use this pattern for model metadata and CSV preview cells:

```javascript
const Safe = window.MedChatSafeRender;

function appendText(parent, tagName, className, value) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  node.textContent = value === null || value === undefined ? "" : String(value);
  parent.appendChild(node);
  return node;
}
```

Build table headers/cells with `document.createElement`, set model names, targets,
SMILES and errors with `textContent`, and retain only fixed labels as template HTML.
Build chat toast/notification icon and message spans separately and assign the
message through `textContent`.

- [ ] **Step 3: Run focused syntax and static tests and verify GREEN**

```powershell
node --check src/web/static/js/activity_prediction/model_manager.js
node --check src/web/static/js/activity_prediction/preflight.js
node --check src/web/static/js/activity_prediction/results_renderer.js
node --check src/web/static/js/activity_prediction/training_monitor.js
node --check src/web/static/js/home/chat_renderer.js
node tests/activity_prediction_safe_render_test.js
node tests/frontend_safe_render_test.js
```

Expected: all commands pass.

- [ ] **Step 4: Commit the rendering fix**

```powershell
git add -- src/web/templates/activity_prediction.html src/web/static/js/activity_prediction/model_manager.js src/web/static/js/activity_prediction/preflight.js src/web/static/js/activity_prediction/results_renderer.js src/web/static/js/activity_prediction/training_monitor.js src/web/static/js/home/chat_renderer.js
git commit -m "fix(web): enforce safe rendering on activity and chat pages"
```

### Task 3: Enforce protected browser callers

**Files:**
- Modify: `src/web/static/js/activity_prediction/model_manager.js`
- Modify: `src/web/templates/molecular_docking.html`
- Modify: `src/web/static/js/docking/ui_manager.js`
- Modify: `tests/admin_fetch_test.js`
- Test: `tests/admin_fetch_test.js`

- [ ] **Step 1: Add failing protected-caller assertions**

Extend the Node test to read the callers and assert:

```javascript
assert(modelManager.includes("window.MedChatAdminAuth.fetch"));
assert(!modelManager.includes('fetch("/api/activity/models'));
assert(dockingTemplate.includes("/static/js/shared/admin_fetch.js"));
assert(dockingUi.includes("window.MedChatAdminAuth.fetch"));
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
node tests/admin_fetch_test.js
```

Expected: failure for the activity-model or docking-history raw fetch path.

- [ ] **Step 3: Route management requests through the shared helper**

Replace the relevant calls with:

```javascript
const Admin = window.MedChatAdminAuth;
const response = await Admin.fetch(url, options);
```

Load `admin_fetch.js` before docking `ui_manager.js`.

- [ ] **Step 4: Verify and commit**

```powershell
node tests/admin_fetch_test.js
node --check src/web/static/js/activity_prediction/model_manager.js
node --check src/web/static/js/docking/ui_manager.js
git add -- tests/admin_fetch_test.js src/web/static/js/activity_prediction/model_manager.js src/web/templates/molecular_docking.html src/web/static/js/docking/ui_manager.js
git commit -m "fix(web): authenticate protected management callers"
```

### Task 4: Map returned workflow failures to failed task state

**Files:**
- Modify: `tests/test_task_runtime.py`
- Modify: `src/task_runtime/manager.py`
- Test: `tests/test_task_runtime.py`

- [ ] **Step 1: Add failing task-state tests**

```python
def test_returned_failure_is_persisted_as_failed(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    record = manager.submit(
        "agent_workflow",
        {},
        lambda _payload: {"status": "failed", "message": "tool unavailable"},
    )
    finished = wait_for_terminal(manager, record.task_id)
    assert finished.status is TaskStatus.FAILED
    assert finished.result["status"] == "failed"
    assert finished.error == "tool unavailable"


def test_success_false_is_persisted_as_failed(tmp_path):
    manager = TaskManager(db_path=tmp_path / "tasks.sqlite", max_workers=1)
    record = manager.submit("science", {}, lambda _: {"success": False, "error": "invalid input"})
    finished = wait_for_terminal(manager, record.task_id)
    assert finished.status is TaskStatus.FAILED
```

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_task_runtime.py -q -p no:cacheprovider
```

Expected: returned failure cases are incorrectly `succeeded`.

- [ ] **Step 3: Add one result-normalization helper**

Implement:

```python
def _terminal_state(result: dict[str, Any]) -> tuple[TaskStatus, str | None]:
    status = str(result.get("status") or "").lower()
    failed = result.get("success") is False or status == "failed"
    if not failed:
        return TaskStatus.SUCCEEDED, None
    message = result.get("error") or result.get("message") or "Task returned a failed result"
    return TaskStatus.FAILED, str(message)
```

Call it from `_run_task` and persist both the structured result and error.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_task_runtime.py tests/test_phase2_phase3_routes.py -q -p no:cacheprovider
git add -- tests/test_task_runtime.py src/task_runtime/manager.py
git commit -m "fix(tasks): preserve returned workflow failure state"
```

## Stage 2 — Agent and docking request isolation

### Task 5: Make Agent events request-scoped

**Files:**
- Modify: `tests/agent/test_workflow_executor.py`
- Modify: `tests/agent/test_chat_handler_agent_events.py`
- Modify: `src/agent/runtime/workflow_executor.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Test: `tests/agent/test_workflow_executor.py`

- [ ] **Step 1: Add a concurrent event-isolation test**

Run two executions against the same injected orchestrator using a barrier-controlled
tool. Capture separate callbacks and assert each callback sees only its own trace:

```python
assert {event["trace_id"] for event in first_events} == {"trace-a"}
assert {event["trace_id"] for event in second_events} == {"trace-b"}
assert shared_orchestrator.event_bus is original_event_bus
```

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_workflow_executor.py -q -p no:cacheprovider
```

Expected: the shared `event_bus` is mutated or event traces cross.

- [ ] **Step 3: Add request-scoped cloning**

Add to `WorkflowOrchestrator`:

```python
def for_request(self, event_bus: AgentEventBus) -> "WorkflowOrchestrator":
    return WorkflowOrchestrator(
        event_bus=event_bus,
        validator=self.validator,
        state_store=self.state_store,
        workflow_version=self.workflow_version,
        adapter_version=self.adapter_version,
    )
```

`WorkflowExecutor` uses `self.orchestrator.for_request(event_bus)` and never assigns
to `self.orchestrator.event_bus`.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_workflow_executor.py tests/agent/test_chat_handler_agent_events.py tests/agent/test_agent_event_stream.py -q -p no:cacheprovider
git add -- tests/agent/test_workflow_executor.py tests/agent/test_chat_handler_agent_events.py src/agent/runtime/workflow_executor.py src/agent/orchestrators/workflow.py
git commit -m "fix(agent): isolate workflow event buses per request"
```

### Task 6: Align delegated tool ownership with chat execution

**Files:**
- Modify: `tests/agent/test_specialist_agents.py`
- Modify: `tests/agent/test_supervisor_delegation.py`
- Modify: `src/agent/tooling/factory.py`
- Modify: `src/agent/specialists/agents.py`
- Test: `tests/agent/test_supervisor_delegation.py`

- [ ] **Step 1: Add failing ownership and comprehensive-workflow tests**

```python
def test_reverse_target_specialist_owns_reverse_prediction():
    assert ReverseTargetAgent.allowed_tools == {"reverse_target_predictor"}
    registry = build_tool_registry([FakeTool("reverse_target_predictor")])
    assert registry.resolve("reverse_target_predictor", agent_name="reverse_target")
```

Add a delegated comprehensive workflow assertion that reverse target runs before
target database search and both results are preserved.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_specialist_agents.py tests/agent/test_supervisor_delegation.py -q -p no:cacheprovider
```

- [ ] **Step 3: Register the specialist**

```python
class ReverseTargetAgent(SpecialistAgent):
    name = "reverse_target"
    allowed_tools = {"reverse_target_predictor"}
```

Add the owner mapping and include the agent in `build_default_specialists()`.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_specialist_agents.py tests/agent/test_supervisor_delegation.py tests/agent/test_comprehensive_workflow.py -q -p no:cacheprovider
git add -- tests/agent/test_specialist_agents.py tests/agent/test_supervisor_delegation.py src/agent/tooling/factory.py src/agent/specialists/agents.py
git commit -m "fix(agent): add reverse-target specialist ownership"
```

### Task 7: Isolate docking jobs and remove synthetic result code

**Files:**
- Modify: `tests/test_docking_configuration.py`
- Modify: `tests/test_docking_history_index.py`
- Modify: `tests/test_agent_anti_hallucination_fallbacks.py`
- Modify: `src/docking/molecular_docking_service.py`
- Modify: `src/docking/adapters/base.py`
- Modify: `src/docking/adapters/vina_adapter.py`
- Modify: `src/docking/history_index.py`
- Modify: `src/agent/tools/molecular_docking.py`

- [ ] **Step 1: Add failing isolation, timeout, and anti-fabrication tests**

Assert `run_vina_docking(..., job_dir=...)` writes `job_dir/config.txt`; run two
temporary jobs and assert different config paths. Add a fake executable that sleeps
past its timeout and assert the adapter reports timeout. Add a static assertion that
`molecular_docking.py` contains none of:

```python
forbidden = ["_generate_binding_mode", "_generate_interactions", "docking_score", "binding_affinity"]
```

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_configuration.py tests/test_docking_history_index.py tests/test_agent_anti_hallucination_fallbacks.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement per-job config and managed timeout**

Change the adapter interface to:

```python
def run(self, args: list[str], cwd: str | None = None, timeout: float | None = None):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        timeout=timeout,
    )
```

Pass a configured Vina timeout and generate the config under the job directory.
Serialize history read-modify-write operations with a module `threading.RLock`.
Delete the unused synthetic docking helper methods and stale hard-coded target map.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_configuration.py tests/test_docking_history_index.py tests/test_docking_agent_architecture.py tests/test_agent_anti_hallucination_fallbacks.py -q -p no:cacheprovider
git add -- tests/test_docking_configuration.py tests/test_docking_history_index.py tests/test_agent_anti_hallucination_fallbacks.py src/docking/molecular_docking_service.py src/docking/adapters/base.py src/docking/adapters/vina_adapter.py src/docking/history_index.py src/agent/tools/molecular_docking.py
git commit -m "fix(docking): isolate jobs and remove synthetic claims"
```

### Task 8: Move heavy synchronous API work off the event loop

**Files:**
- Modify: `tests/test_docking_agent_architecture.py`
- Modify: `tests/test_reverse_target_health.py`
- Modify: `src/web/routes/api_routes.py`
- Modify: `src/target_search/routes.py`

- [ ] **Step 1: Add failing responsiveness contracts**

Patch the synchronous service calls with functions that record thread identity and
assert the route executes them through `run_in_threadpool` rather than the request
event-loop thread. Cover direct docking, reverse-target prediction, batch prediction,
and structure download.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_agent_architecture.py tests/test_reverse_target_health.py tests/test_target_search.py -q -p no:cacheprovider
```

- [ ] **Step 3: Offload synchronous work**

Use:

```python
from starlette.concurrency import run_in_threadpool

result = await run_in_threadpool(service_method, *args, **kwargs)
```

Use `_read_upload_limited` for reverse-target batch files and bound image width,
height, MCS timeout, and report base64 payload size with FastAPI `Query`/validation.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_agent_architecture.py tests/test_reverse_target_health.py tests/test_target_search.py -q -p no:cacheprovider
git add -- tests/test_docking_agent_architecture.py tests/test_reverse_target_health.py src/web/routes/api_routes.py src/target_search/routes.py
git commit -m "fix(api): offload heavy scientific work from event loop"
```

## Stage 3 — RAG and scientific-model contracts

### Task 9: Add a versioned RAG manifest and row mapping

**Files:**
- Create: `src/web/rag_index.py`
- Create: `tests/test_rag_index_manifest.py`
- Modify: `src/web/app.py`
- Test: `tests/test_rag_index_manifest.py`

- [ ] **Step 1: Add failing skipped-row and stale-manifest tests**

Build three source rows with the middle embedding returning empty. Assert the index
mapping is `[0, 2]` and search result index `1` resolves source row `2`. Write a
manifest with the wrong CSV SHA256 and assert loading returns an explicit
incompatible status.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_rag_index_manifest.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement the manifest model and atomic persistence**

Use a dataclass with this serialized shape:

```python
@dataclass(frozen=True)
class RAGIndexManifest:
    schema_version: int
    source_path: str
    source_sha256: str
    embedding_model: str
    vector_dimension: int
    vector_count: int
    row_mapping: list[int]
    created_at: str
    builder_version: str = "1"
```

Write index and manifest to temporary paths, validate counts/dimension, then replace
the final files. `RAGSystem.search_similar_molecules` resolves FAISS labels through
the manifest row mapping.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_rag_index_manifest.py tests/agent/test_tool_adapter_compat.py -q -p no:cacheprovider
git add -- src/web/rag_index.py tests/test_rag_index_manifest.py src/web/app.py
git commit -m "feat(rag): validate index provenance and row mapping"
```

### Task 10: Enforce the RAG feature flag in Agent execution

**Files:**
- Modify: `tests/agent/test_chat_handler_agent_events.py`
- Modify: `src/web/chat_handler.py`
- Modify: `src/agent/supervisor.py`
- Modify: `src/agent/contracts/context.py`

- [ ] **Step 1: Add a failing RAG-disabled Agent test**

Send a knowledge-base prompt with `enable_rag=False`, keep tools enabled, and assert
`rag_search` is absent from tools used and no RAG event is emitted. Send the same
prompt with `enable_rag=True` and assert routing remains available.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_chat_handler_agent_events.py -q -p no:cacheprovider
```

- [ ] **Step 3: Propagate capabilities into context**

Pass metadata:

```python
{"capabilities": {"rag": bool(enable_rag), "scientific_tools": bool(enable_tools)}}
```

Before execution, remove `rag_search` and its alias when the RAG capability is false.
Do not rely only on Router abstention.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_chat_handler_agent_events.py tests/agent/test_routing_prompt_matrix.py -q -p no:cacheprovider
git add -- tests/agent/test_chat_handler_agent_events.py src/web/chat_handler.py src/agent/supervisor.py src/agent/contracts/context.py
git commit -m "fix(agent): enforce RAG capability at execution boundary"
```

### Task 11: Introduce a confined activity-model registry

**Files:**
- Create: `src/activity/model_registry.py`
- Create: `tests/test_activity_model_registry.py`
- Modify: `src/activity/trainer.py`
- Modify: `src/activity/predictor.py`
- Modify: `src/web/routes/api_routes.py`

- [ ] **Step 1: Add failing path-confinement and metadata tests**

```python
def test_registry_rejects_model_outside_models_dir(tmp_path):
    registry = ActivityModelRegistry(tmp_path / "models")
    with pytest.raises(ValueError, match="registered model"):
        registry.select("../../untrusted.pt")


def test_registry_requires_scientific_endpoint_metadata(tmp_path):
    registry = ActivityModelRegistry(tmp_path / "models")
    with pytest.raises(ValueError, match="endpoint"):
        registry.register({"weights_file": "model.pt", "task_type": "regression"})
```

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_model_registry.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement registry validation**

The required registration fields are:

```python
REQUIRED_MODEL_FIELDS = {
    "model_id", "weights_file", "task_type", "endpoint", "units",
    "dataset_sha256", "split_strategy", "random_seed", "model_config",
}
```

Resolve the selected file and require `resolved_path.is_relative_to(models_dir)`.
Validate it exists and matches the registered filename. The switch API accepts a
model ID, not an arbitrary path. Load checkpoints with `weights_only=True` when the
installed PyTorch signature supports it.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_model_registry.py tests/test_rg_mpnn_legacy_modules.py tests/test_admin_auth_routes.py -q -p no:cacheprovider
git add -- src/activity/model_registry.py tests/test_activity_model_registry.py src/activity/trainer.py src/activity/predictor.py src/web/routes/api_routes.py
git commit -m "fix(activity): confine and validate model selection"
```

### Task 12: Make activity inference task-aware and split-aware

**Files:**
- Create: `tests/test_activity_prediction_contract.py`
- Modify: `src/activity/predictor.py`
- Modify: `src/activity/trainer.py`
- Modify: `src/web/routes/api_routes.py`
- Modify: `src/web/static/js/activity_prediction/main.js`
- Modify: `src/agent/tools/activity_predictor_tool.py`

- [ ] **Step 1: Add failing regression/classification contract tests**

Assert a regression checkpoint returns:

```python
{"endpoint": "pIC50", "value": 6.2, "units": "log10(mol/L)", "task_type": "regression"}
```

Assert a classification logit is transformed through sigmoid and returns
`probability`, never generic `activity_score`, `confidence: 1.0`, or pIC50. Assert
an unknown endpoint checkpoint fails before inference. Assert `split_strategy`
posted by the UI reaches the trainer and is stored in model metadata.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_prediction_contract.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement explicit prediction schemas and split dispatch**

Regression output:

```python
{
    "smiles": smiles,
    "success": True,
    "task_type": "regression",
    "endpoint": metadata["endpoint"],
    "value": float(raw_output),
    "units": metadata["units"],
}
```

Classification output uses `torch.sigmoid(raw_output)` and records probability.
Implement `random` and `scaffold` split helpers; use scaffold split by default for
new molecular training jobs while retaining explicit `random` compatibility.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_activity_prediction_contract.py tests/test_rg_mpnn_legacy_modules.py tests/test_agent_anti_hallucination_fallbacks.py -q -p no:cacheprovider
node --check src/web/static/js/activity_prediction/main.js
git add -- tests/test_activity_prediction_contract.py src/activity/predictor.py src/activity/trainer.py src/web/routes/api_routes.py src/web/static/js/activity_prediction/main.js src/agent/tools/activity_predictor_tool.py
git commit -m "feat(activity): preserve endpoint and task semantics"
```

### Task 13: Complete scientific provenance and real dataflow checks

**Files:**
- Modify: `tests/agent/test_domain_result_validators.py`
- Modify: `tests/agent/test_real_acceptance_checks.py`
- Modify: `src/agent/tools/admet_predictor.py`
- Modify: `src/agent/tools/reverse_target_tool.py`
- Modify: `src/agent/tools/target_database_tool.py`
- Modify: `src/agent/evaluation/scientific.py`

- [ ] **Step 1: Add failing provenance and consumption tests**

Cover:

- `adme_py` success includes `prediction_method="adme_py"` and package version;
- reverse-target records include a stable target identifier, assay relation and
  units when available;
- target search returns recommended structure records, not only counts;
- a plan with `input_from="molecules"` but downstream output lacking generated
  canonical SMILES fails the dataflow check;
- structured provenance score requires non-empty input summary/hash, output summary,
  tool/model identity and event trace.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_domain_result_validators.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement evidence-first checks**

Evaluate actual downstream canonical-SMILES intersection before considering plan
wiring. Plan wiring alone produces `partial`, never `passed`. Build provenance from
the persisted tool execution input hash/summary rather than `ToolResult` fields that
do not contain input.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_domain_result_validators.py tests/agent/test_real_acceptance_checks.py tests/agent/test_prompt_acceptance.py -q -p no:cacheprovider
git add -- tests/agent/test_domain_result_validators.py tests/agent/test_real_acceptance_checks.py src/agent/tools/admet_predictor.py src/agent/tools/reverse_target_tool.py src/agent/tools/target_database_tool.py src/agent/evaluation/scientific.py
git commit -m "fix(science): require complete provenance and observed dataflow"
```

## Stage 4 — Deployment, CI, and controlled cleanup

### Task 14: Make SQLite, configuration and logs deployment-safe

**Files:**
- Modify: `tests/test_target_search.py`
- Modify: `tests/test_deployment_assets.py`
- Modify: `src/target_search/database.py`
- Modify: `src/target_search/downloader.py`
- Modify: `main.py`
- Modify: `data/REGISTRY.md`
- Modify: `deployment/README.md`

- [ ] **Step 1: Add failing deployment contracts**

Assert target DB connections use WAL, foreign keys and a busy timeout, and do not use
`journal_mode=OFF` or `locking_mode=EXCLUSIVE`. Assert `TARGET_DB_PATH` and
`TARGET_CACHE_DIR` affect runtime paths. Assert downloaded structures are written
to a temporary path and only replace the final path after validation.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_target_search.py tests/test_deployment_assets.py -q -p no:cacheprovider
```

- [ ] **Step 3: Implement safe SQLite and atomic download defaults**

Apply:

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=30000")
conn.execute("PRAGMA foreign_keys=ON")
```

Resolve DB/cache paths from environment variables. Stream downloads with a maximum
byte count, validate expected structure text markers, then use `Path.replace`.
Change application logs from truncating `FileHandler(mode="w")` to a rotating
handler with append semantics.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_target_search.py tests/test_deployment_assets.py -q -p no:cacheprovider
git add -- tests/test_target_search.py tests/test_deployment_assets.py src/target_search/database.py src/target_search/downloader.py main.py data/REGISTRY.md deployment/README.md
git commit -m "fix(deploy): harden sqlite paths downloads and logs"
```

### Task 15: Add reproducible quality CI

**Files:**
- Create: `.github/workflows/quality.yml`
- Modify: `requirements.txt`
- Modify: `deployment/requirements.txt`
- Modify: `docs/PROJECT_STANDARDS.md`
- Test: `tests/test_deployment_assets.py`

- [ ] **Step 1: Add a failing CI asset contract**

Assert the workflow exists and contains Python tests, Node tests, compile checks,
secret scanning, and no real API key requirement.

- [ ] **Step 2: Run and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_deployment_assets.py -q -p no:cacheprovider
```

- [ ] **Step 3: Add the workflow and compatibility matrix**

The workflow runs on Python 3.10 and executes:

```yaml
- run: python -m pytest tests -q -p no:cacheprovider
- run: python -m compileall -q src scripts
- run: node tests/admin_fetch_test.js
- run: node tests/frontend_safe_render_test.js
- run: node tests/activity_prediction_safe_render_test.js
- run: node tests/home_agent_task_panel_test.js
- run: node tests/reverse_target_broad_recall_test.js
- run: node tests/reverse_target_pagination_test.js
- run: git grep -nE 'sk-[A-Za-z0-9]{20,}|BEGIN (RSA|OPENSSH) PRIVATE KEY' -- . ':!*.md'
```

Document root/deployment version differences as explicit profiles; do not silently
claim they are interchangeable.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_deployment_assets.py -q -p no:cacheprovider
git add -- .github/workflows/quality.yml requirements.txt deployment/requirements.txt docs/PROJECT_STANDARDS.md tests/test_deployment_assets.py
git commit -m "ci: add reproducible quality and secret checks"
```

### Task 16: Remove only proven-dead duplicate implementations

**Files:**
- Modify: `tests/test_agent_platform_health_check.py`
- Delete after proof: `src/web/rag_service.py`
- Delete after proof: `src/rag/molecular_rag.py`
- Delete after proof: `src/web/models.py`
- Delete after proof: `src/web/static/js/script.legacy.backup.js`
- Modify: `src/web/app.py`
- Modify: `src/agent/evaluation/scientific.py`

- [ ] **Step 1: Add an import and route ownership contract**

Assert the active RAG and Ollama classes have one canonical import path and no
tracked production module imports the deletion candidates. Assert all expected
routes remain present on the FastAPI app.

- [ ] **Step 2: Run the contract before deletion**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_agent_platform_health_check.py tests/test_phase2_phase3_routes.py -q -p no:cacheprovider
```

Expected: failure while duplicate canonical implementations remain.

- [ ] **Step 3: Redirect remaining imports and delete proven-dead files**

Keep `src/web/models/ollama_model.py` as the canonical local model client and the
new manifest-backed RAG service as the canonical retrieval implementation. Remove
only candidates with zero runtime imports after the tests are in place.

- [ ] **Step 4: Verify and commit**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_agent_platform_health_check.py tests/test_phase2_phase3_routes.py tests/agent -q -p no:cacheprovider
git add -u -- src/web/rag_service.py src/rag/molecular_rag.py src/web/models.py src/web/static/js/script.legacy.backup.js
git add -- tests/test_agent_platform_health_check.py src/web/app.py src/agent/evaluation/scientific.py
git commit -m "refactor(runtime): remove superseded rag and model implementations"
```

## Final verification

### Task 17: Run all quality gates and produce the handoff

**Files:**
- Modify: `docs/handoff/latest.md`
- Runtime output only: `outputs/agent_evaluation/agent_acceptance_report.json`

- [ ] **Step 1: Run the complete Python suite**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests -q -p no:cacheprovider
```

Expected: all tests pass with no new project warnings.

- [ ] **Step 2: Run syntax and all Node checks**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
Get-ChildItem src/web/static/js -Recurse -Filter *.js -File |
  Where-Object { $_.FullName -notmatch 'ketcher|backup' } |
  ForEach-Object { node --check $_.FullName }
Get-ChildItem tests -File -Filter *.js | ForEach-Object { node $_.FullName }
```

- [ ] **Step 3: Run contract and honest real-tool acceptance**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts/run_agent_acceptance.py --mode contract
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts/run_agent_acceptance.py --mode real --case-set golden --repeat 3
```

Expected: contract passes; real results may be passed, partial, skipped, or failed
according to actual local dependencies. No missing dependency is converted to pass.

- [ ] **Step 4: Update handoff and perform safety checks**

Record exact files, commands, pass counts, real-tool statuses, known limitations,
and the fact that credentials came only from runtime environment variables. Run:

```powershell
git diff --check
git status --short
git diff --cached --name-only
git grep -nE 'sk-[A-Za-z0-9]{20,}|BEGIN (RSA|OPENSSH) PRIVATE KEY' -- . ':!*.md'
```

- [ ] **Step 5: Commit the handoff**

```powershell
git add -- docs/handoff/latest.md
git commit -m "docs: record scientific runtime hardening verification"
```

## Plan self-review results

- Every design section maps to one or more tasks above.
- Every behavior-changing task begins with an explicit failing test and RED command.
- New types and field names are consistent across later tasks.
- The plan preserves current public response fields while adding explicit provenance.
- No task requires a real API key in source, tests, logs, or reports.
