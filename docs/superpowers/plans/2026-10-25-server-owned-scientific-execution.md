# Server-owned Scientific Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make explicit molecular-analysis requests execute through real, evidence-bound tools while the frontend-selected model remains the Agent model and `gmm-llama:latest` remains exclusively the molecular-generation model.

**Architecture:** Add a typed, server-owned property-obligation compiler in front of the existing Pi/Web runtime. The compiler only produces reviewed `WorkflowStep` values for validated property and drug-likeness obligations; `WorkflowOrchestrator`/`WorkflowRunSession` remain the execution and provenance authority. Separately, refresh the homepage's Agent model label and readiness status from the authoritative LLM configuration and Agent probe instead of treating WebSocket connectivity as model readiness.

**Tech Stack:** FastAPI, Pydantic 2, existing Pi runtime, `WorkflowOrchestrator`, `WorkflowRunSession`, RDKit-backed tools, Jinja2, native JavaScript, Node `node:test`, pytest.

---

## File map

- Create: `src/web/scientific_obligations.py` — typed admission and compilation for the first property-analysis profile.
- Create: `tests/agent/test_scientific_obligations.py` — red/green compiler and plan-boundary tests.
- Create: `tests/agent/test_web_scientific_execution.py` — WebSocket compiled-plan execution and terminal-state tests.
- Modify: `src/web/decision_runtime.py` — invoke compiled plans inside the existing request ownership and event lifecycle.
- Modify: `src/web/chat_handler.py` — route a ready compiled plan and preserve current failure/clarification envelopes.
- Modify: `src/web/agent_result_presentation.py` — project plan and evidence-bound observation fields without inventing values.
- Modify: `src/web/app.py` — expose additive Agent role/capability metadata and retain main/generator model separation.
- Modify: `src/web/static/js/home/main.js` — refresh the actual Agent model label and distinguish WebSocket transport from Agent readiness.
- Modify: `src/web/templates/index.html` — add accessible role/readiness labels without adding a frontend framework.
- Modify: `tests/home_llm_settings_test.js` — homepage model identity/readiness contract tests.
- Modify: `tests/agent/test_web_decision_runtime.py` or the nearest existing Web runtime contract module — execution and lifecycle regression tests.

## Task 1: Lock the model-role and homepage-status contract

**Files:**
- Modify: `src/web/app.py:1280-1292` and the `/api/llm/config` route near `src/web/app.py:892-901`.
- Modify: `src/web/static/js/home/main.js:385-420, 620-675, 2943-2962`.
- Modify: `src/web/templates/index.html:2130-2199`.
- Test: `tests/home_llm_settings_test.js`.

- [ ] **Step 1: Write the failing JavaScript tests.**

Add tests that execute the production declarations and assert:

```js
test("configured Agent model replaces the stale toolbar default", async () => {
  const { context, elements } = harness([
    { success: true, config: {
      provider: "ollama",
      base_url: "http://127.0.0.1:11434",
      model_name: "agent-model",
      stream: true,
    } },
  ]);
  await context.refreshAgentModelStatus();
  assert.equal(elements.modelSelect.value, "agent-model");
  assert.match(elements.connectionStatus.textContent, /Agent/);
});

test("WebSocket ready does not claim Agent readiness", () => {
  const { context, elements } = harness();
  context.updateConnectionStatus("connected");
  assert.match(elements.connectionStatus.textContent, /传输已连接/);
  assert.doesNotMatch(elements.connectionStatus.textContent, /模型已就绪/);
});

test("failed Agent probe is shown separately from transport connectivity", async () => {
  const { context, elements } = harness([
    { success: true, config: { provider: "ollama", model_name: "agent-model" } },
    { success: false, message: "决策接口不可用" },
  ]);
  await context.refreshAgentModelStatus();
  await context.refreshAgentReadiness();
  assert.match(elements.connectionStatus.textContent, /Agent.*不可用/);
});
```

Expose only the production functions needed by the existing VM harness; do not
duplicate their implementation in the test.

- [ ] **Step 2: Run the focused test and verify it fails for the intended reason.**

Run:

```powershell
node --test tests/home_llm_settings_test.js
```

Expected: FAIL because the production homepage has no authoritative Agent
model refresh/readiness functions and still maps `connected` to `已连接`.

- [ ] **Step 3: Add the additive backend metadata.**

Extend `/api/llm/config` with nonsecret fields:

```python
"agent": {
    "provider": self.active_llm_config["provider"],
    "model_name": self.model.model_name,
    "role": "agent",
    "generator_model_name": self.molecular_generator_model.model_name,
    "readiness": "unknown",
}
```

The existing `config` object remains unchanged for compatibility. The
generator name is metadata only; no API path may substitute that object for
`self.model`.

- [ ] **Step 4: Implement the minimal frontend projection.**

Add production functions with these exact responsibilities:

```js
async function refreshAgentModelStatus() {
  const response = await fetch("/api/llm/config");
  const result = await response.json();
  if (!result.success) throw new Error(result.message || "读取模型配置失败");
  const agent = result.agent || result.config || {};
  applyAgentModelLabel(agent);
  setAgentReadiness("unknown");
}

function applyAgentModelLabel(agent) {
  const name = String(agent.model_name || "未配置 Agent 模型");
  if (elements.modelSelect) {
    let option = Array.from(elements.modelSelect.options || []).find(item => item.value === name);
    if (!option) {
      option = document.createElement("option");
      option.value = name;
      elements.modelSelect.appendChild(option);
    }
    option.textContent = name;
    elements.modelSelect.value = name;
    elements.modelSelect.setAttribute("aria-label", `Agent 模型：${name}`);
  }
}

async function refreshAgentReadiness() {
  const response = await fetch("/api/llm/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collectLlmSettingsForm()),
  });
  const result = await response.json();
  setAgentReadiness(result.success ? "ready" : "unavailable");
  return result;
}

function setAgentReadiness(state) {
  const labels = {
    unknown: ["🟡 Agent 待测试", "#fbbf24"],
    ready: ["🟢 Agent 可用", "#10b981"],
    unavailable: ["🔴 Agent 不可用", "#ef4444"],
  };
  const [text, backgroundColor] = labels[state] || labels.unknown;
  elements.connectionStatus.textContent = text;
  elements.connectionStatus.style.backgroundColor = backgroundColor;
}
```

The initial page load calls `refreshAgentModelStatus()` after element binding.
`connection_ready` calls a transport-only renderer; it must not call
`setAgentReadiness("ready")`. The settings test endpoint sets `ready` only for
an actual successful Agent decision probe and `unavailable` for its failure.

- [ ] **Step 5: Run the focused tests and JavaScript syntax check.**

Run:

```powershell
node --test tests/home_llm_settings_test.js
node --check src/web/static/js/home/main.js
```

Expected: all focused tests pass; the page no longer shows a stale DeepSeek
label or a model-ready claim based solely on WebSocket transport.

- [ ] **Step 6: Commit the isolated UI contract change.**

```powershell
git add src/web/app.py src/web/static/js/home/main.js src/web/templates/index.html tests/home_llm_settings_test.js
git commit -m "fix(web): distinguish Agent readiness from transport status"
```

## Task 2: Add the typed property-obligation compiler

**Files:**
- Create: `src/web/scientific_obligations.py`.
- Create: `tests/agent/test_scientific_obligations.py`.
- Test: existing `tests/agent/test_plan_compiler.py` for compatibility.

- [ ] **Step 1: Write the failing compiler tests.**

Use the public compiler API below and assert the exact plan boundary:

```python
def test_compiles_ccо_properties_to_one_reviewed_step():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质，返回 LogP 和 TPSA。"
    )
    assert plan.status == "ready"
    assert plan.smiles == "CCO"
    assert plan.required_tools == ("property_calculator",)
    assert plan.metrics == ("logp", "tpsa")
    assert plan.steps[0].tool_name == "property_calculator"
    assert plan.steps[0].input_data == "CCO"

def test_compiles_explicit_drug_likeness_with_properties():
    plan = ScientificObligationCompiler().compile(
        "请分析 CCO 的分子性质并评估类药性。"
    )
    assert plan.required_tools == ("property_calculator", "drug_likeness_assessment")

def test_missing_or_invalid_smiles_is_clarification_without_steps():
    plan = ScientificObligationCompiler().compile("请计算这个分子的 LogP")
    assert plan.status == "clarification"
    assert plan.missing_fields == ("smiles",)
    assert plan.steps == ()

def test_unsupported_obligation_is_rejected_without_partial_property_plan():
    plan = ScientificObligationCompiler().compile("请分析 CCO 的 ADMET 和 LogP")
    assert plan.status == "rejected"
    assert plan.missing_capabilities == ("admet",)
    assert plan.steps == ()

def test_model_cannot_supply_authoritative_tool_names_or_smiles():
    with pytest.raises(ScientificObligationError, match="authoritative"):
        ScientificObligationCompiler().compile(
            "CCO", model_fields={"smiles": "CCN", "required_tools": ["rag_search"]}
        )
```

The test fixture may use real RDKit parsing; it must not mock RDKit success.

- [ ] **Step 2: Run the compiler tests and confirm the expected RED failure.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_scientific_obligations.py -q
```

Expected: FAIL because `ScientificObligationCompiler` and its typed plan do not
yet exist.

- [ ] **Step 3: Implement the typed DTOs and narrow parser.**

Implement strict immutable Pydantic models:

```python
class ScientificPlan(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    status: Literal["ready", "clarification", "rejected"]
    original_query: str
    smiles: str | None = None
    canonical_smiles: str | None = None
    metrics: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    steps: tuple[WorkflowStep, ...] = ()
    workflow_version: str = "web-property-v1"
```

`ScientificObligationCompiler.compile()` must:

1. Bound query length before parsing.
2. Extract and validate SMILES with the existing molecular input parser and
   RDKit boundary; reject invalid structures without running a tool.
3. Recognize only the reviewed metrics `logp`, `tpsa`, `mw`, `qed`, `hba`,
   `hbd`, and `rotatable_bonds`.
4. Detect ADMET, docking, generation/ranking, target-search and reverse-target
   obligations and reject them as unsupported rather than dropping them.
5. Emit `WorkflowStep("properties", "property_calculator", input_data=smiles,
   output_key="properties", capability="molecular_properties")` and, when
   explicitly requested, a second required drug-likeness step with the exact
   SMILES input.
6. Never read tool names, SMILES, metrics, user identity or capabilities from
   model output.

- [ ] **Step 4: Run compiler and plan-compiler regressions.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_scientific_obligations.py tests/agent/test_plan_compiler.py -q
```

Expected: all new tests and existing plan-compiler tests pass.

- [ ] **Step 5: Commit the compiler contract.**

```powershell
git add src/web/scientific_obligations.py tests/agent/test_scientific_obligations.py
git commit -m "feat(agent): compile bounded molecular obligations"
```

## Task 3: Execute compiled plans through the existing session authority

**Files:**
- Modify: `src/web/decision_runtime.py:937-1110`.
- Modify: `src/web/chat_handler.py:261-298, 704-838`.
- Modify: `src/web/agent_result_presentation.py:197-373`.
- Test: `tests/agent/test_web_scientific_execution.py`.

- [ ] **Step 1: Write failing runtime tests.**

Add a real-tool integration fixture with `PropertyCalculator` and
`DrugLikenessAssessment`, a temporary SQLite state store, and a fake model only
for the post-tool summary. Assert:

```python
async def test_ready_property_plan_executes_real_tools_and_publishes_evidence(...):
    frames = await socket.turn({"message": "请分析 CCO 的分子性质，返回 LogP 和 TPSA。"})
    result = result_of(frames)
    assert result["status"] == "completed"
    assert result["metadata"]["required_tools"] == ["property_calculator"]
    assert result["evidence"]
    assert result["observations"][0]["status"] == "succeeded"
    assert "gmm-llama:latest" not in json.dumps(result, ensure_ascii=False)

async def test_clarification_plan_runs_no_tools(...):
    frames = await socket.turn({"message": "请计算这个分子的 LogP"})
    result = result_of(frames)
    assert result["status"] == "waiting_for_input"
    assert result["metadata"]["stop_reason"] == "scientific_input_missing"
    assert no_tool_start_events(frames)

async def test_unsupported_mixed_request_is_not_partial_success(...):
    frames = await socket.turn({"message": "请分析 CCO 的 ADMET 和 LogP"})
    result = result_of(frames)
    assert result["status"] == "rejected"
    assert result["metadata"]["missing_capabilities"] == ["admet"]
    assert no_tool_start_events(frames)
```

- [ ] **Step 2: Run the new runtime tests and verify they fail.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_web_scientific_execution.py -q
```

Expected: FAIL because the current Web decision path waits for a model action
instead of dispatching a compiled plan.

- [ ] **Step 3: Add the compiled-plan execution seam.**

Before the existing `handler.process_decision_message()` call in the Web
request segment:

1. Compile the user query with `ScientificObligationCompiler`.
2. For `clarification` or `rejected`, publish the existing structured terminal
   state with `stop_reason`, `missing_fields` or `missing_capabilities` and do
   not acquire a tool worker.
3. For `ready`, construct an `AgentContext` using the server-owned exact
   molecule and current frontend Agent model metadata.
4. Build the reviewed `WorkflowStep` tuple and call the existing
   `WorkflowOrchestrator.create_session()`/`WorkflowRunSession` under the
   current model lease, cancellation event, deadline and session owner.
5. Publish `plan_created`, `tool_started`, `tool_completed` and terminal events
   through the existing event bus. Do not create a second persistence store.
6. Evaluate required-step success from normalized `ToolResult.status`,
   `evidence`, `provenance` and `warnings`. `partial`, `failed`, `unavailable`,
   timeout and cancellation cannot satisfy a ready obligation.
7. Pass only accepted observation projections to the configured frontend Agent
   model for optional summary generation. If that summary fails, retain the
   accepted tool result and render `summary_unavailable` separately.

The generator model is not passed to this seam. It remains available only in
the existing `llm_molecular_generator` tool path.

- [ ] **Step 4: Add safe result projection.**

Extend the existing presentation DTO to include only bounded fields:

```python
{
    "required_tools": ["property_calculator"],
    "steps": [{"tool_name": "property_calculator", "status": "succeeded"}],
    "observations": [{"tool_name": "property_calculator", "status": "succeeded", "evidence_ids": [...]}],
    "provenance": [{"tool_name": "property_calculator", "model_name": None}],
}
```

The frontend projector must omit data from non-succeeded observations and keep
the existing failure-message allowlist.

- [ ] **Step 5: Run the runtime and presentation regressions.**

Run:

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_web_scientific_execution.py tests/agent/test_workflow_status_presentation.py tests/agent/test_chat_handler_agent_events.py -q
```

Expected: all focused runtime/presentation tests pass, including existing
partial/failure/cancellation behavior.

- [ ] **Step 6: Commit the execution integration.**

```powershell
git add src/web/decision_runtime.py src/web/chat_handler.py src/web/agent_result_presentation.py tests/agent/test_web_scientific_execution.py tests/agent/test_workflow_status_presentation.py tests/agent/test_chat_handler_agent_events.py
git commit -m "feat(agent): execute explicit scientific obligations"
```

## Task 4: Real browser acceptance and regression gate

**Files:**
- Modify: `tests/home_llm_settings_test.js` only if the final wire labels need an additional contract assertion.
- Modify: `docs/PI_RUNTIME.md` with sanitized evidence only.

- [ ] **Step 1: Run offline quality gates.**

```powershell
.venv-ui-test\Scripts\python.exe -m pytest --noconftest tests/agent/test_scientific_obligations.py tests/agent/test_web_scientific_execution.py tests/agent/test_workflow_status_presentation.py tests/test_user_llm_routes.py -q
node --test tests/home_llm_settings_test.js tests/home_decision_runtime_test.js tests/frontend_safe_render_test.js
node --check src/web/static/js/home/main.js
.venv-ui-test\Scripts\python.exe -m compileall -q src tests
git diff --check
```

Expected: all commands pass; any existing warnings are reported without being
reclassified as failures.

- [ ] **Step 2: Start the real application using the user's frontend Agent configuration.**

Do not change the persisted user configuration. Verify the redacted
configuration tuple contains provider/model/base URL host and separately verify
that the generator model is `gmm-llama:latest`.

- [ ] **Step 3: Exercise the real browser flow.**

Verify the visible page shows the actual Agent model, transport status and
Agent readiness separately. Submit:

```text
请分析 CCO 的分子性质，返回 LogP 和 TPSA。
```

Verify the plan, real tool execution, evidence-bound values and provenance.
Then verify missing-SMILES clarification, unsupported ADMET/property rejection,
and provider-unavailable failure. Run a separate molecule-generation request
and verify its provenance identifies `gmm-llama:latest`.

- [ ] **Step 4: Record sanitized evidence and cleanup.**

Record trace IDs, status codes, tool names, model role names and version
metadata only. Do not record API keys, raw chat dumps, private configuration
files, absolute secret-bearing paths or ignored runtime outputs. Stop the test
server and remove only explicitly created empty test directories.

- [ ] **Step 5: Commit the acceptance documentation.**

```powershell
git add docs/PI_RUNTIME.md
git commit -m "docs: record real Agent execution acceptance"
```

## Plan self-review

- The design's model-role requirement is covered by Tasks 1 and 4; the
  generator is never passed into compiled Agent execution.
- The explicit property profile, clarification, unsupported-obligation rejection,
  provenance and evidence requirements are covered by Tasks 2 and 3.
- The stale model label and misleading WebSocket-ready state observed in the
  real browser are covered by Task 1.
- Native Pi transport and existing legacy rejection remain unchanged; no task
  introduces a legacy fallback or a second workflow engine.
- No unresolved placeholder or undefined implementation item is required by
  the plan. Every task has explicit files, tests and commands.
