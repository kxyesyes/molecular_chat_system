# Platform V1 Quality Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a trustworthy Linux CI gate and fix Agent timeout, asynchronous routing, replay, and status metrics before Platform V1 can merge.

**Architecture:** Keep the current Agent stack and add narrow boundary corrections. A dedicated CPU CI profile supplies collection-time dependencies; ToolAdapter returns at its deadline; routing carries the model as request-local state; replay recomputes only contracts supported by persisted report fields and never upgrades an original failure.

**Tech Stack:** Python 3.10, pytest, FastAPI/WebSocket, asyncio, concurrent.futures, GitHub Actions, Node.js 20.

---

### Task 1: Make the offline CI profile collect and run the intended suite

**Files:**
- Create: `requirements-ci.txt`
- Create: `tests/test_quality_workflow_contract.py`
- Modify: `.github/workflows/quality.yml:39-57`

- [ ] **Step 1: Write the failing CI contract tests**

Add tests that require the workflow to install `requirements-ci.txt`, require that profile to include the root and Temporal profiles plus pinned CPU graph dependencies, and require Node tests to be enumerated instead of a six-file list:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_quality_workflow_uses_complete_cpu_profile() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")
    assert "python -m pip install -r requirements-ci.txt" in workflow


def test_cpu_ci_profile_contains_collection_dependencies() -> None:
    requirements = (ROOT / "requirements-ci.txt").read_text("utf-8")
    for expected in (
        "-r requirements.txt",
        "-r requirements-agent-temporal.txt",
        "torch==2.4.0",
        "torch-geometric==2.6.1",
    ):
        assert expected in requirements


def test_quality_workflow_runs_every_tracked_node_contract() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")
    assert "find tests -maxdepth 1 -type f -name '*_test.js'" in workflow
    assert 'node "$test_file"' in workflow
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m pytest tests/test_quality_workflow_contract.py -q -p no:cacheprovider
```

Expected: failures because `requirements-ci.txt` and dynamic Node enumeration do not exist.

- [ ] **Step 3: Add the minimal CPU CI profile**

Create:

```text
-r requirements.txt
-r requirements-agent-temporal.txt

# CPU-only graph imports exercised by tests/test_rg_mpnn_legacy_modules.py.
torch==2.4.0
torch-geometric==2.6.1
```

Do not include `requirements-opensandbox-broker.txt`; it pins `httpx==0.27.0` while the root profile pins `httpx==0.25.2`.

- [ ] **Step 4: Update the workflow**

Change the dependency install command to `python -m pip install -r requirements-ci.txt`. Replace the fixed Node list with:

```bash
while IFS= read -r test_file; do
  node "$test_file"
done < <(find tests -maxdepth 1 -type f -name '*_test.js' -print | sort)
```

- [ ] **Step 5: Verify GREEN and dependency resolution**

Run the new contract test. Then create a temporary virtual environment under `scratch/ci-venv`, install `requirements-ci.txt`, and run `python -m pytest --collect-only tests -q -p no:cacheprovider`. Expected: no import errors for torch, torch_geometric, temporalio, or prometheus_client.

- [ ] **Step 6: Commit the CI fix**

```powershell
git add -- requirements-ci.txt .github/workflows/quality.yml tests/test_quality_workflow_contract.py
git commit -m "ci: install complete platform test dependencies"
```

### Task 2: Make ToolAdapter deadlines observable and prompt

**Files:**
- Modify: `tests/agent/test_tool_adapters.py`
- Modify: `src/agent/tooling/adapters.py:92-117`

- [ ] **Step 1: Write the failing deadline test**

Use a `threading.Event` to hold a real adapter invocation. Call `execute()` in the test thread, assert it returns `TOOL_TIMEOUT` in less than 0.20 seconds for a 0.03-second timeout, assert one invocation, and release the event in `finally`.

```python
def test_adapter_timeout_returns_without_waiting_for_running_tool() -> None:
    release = threading.Event()
    calls = 0

    class BlockingTool:
        name = "legacy_value"

        def execute(self, query):
            nonlocal calls
            calls += 1
            release.wait(1.0)
            return {"success": True, "data": {"value": 1}}

    started = time.monotonic()
    try:
        result = LegacyPythonToolAdapter(
            make_spec(timeout_seconds=0.03), BlockingTool()
        ).execute({"query": "CCO"})
        assert time.monotonic() - started < 0.20
        assert result.error.code == AgentErrorCode.TOOL_TIMEOUT
        assert calls == 1
    finally:
        release.set()
```

- [ ] **Step 2: Verify RED**

Run only this test. Expected: elapsed assertion fails because executor context exit waits for the tool.

- [ ] **Step 3: Implement non-waiting timeout cleanup**

Construct `ThreadPoolExecutor` explicitly. On timeout call `future.cancel()` and return the structured error. In `finally`, call `executor.shutdown(wait=False, cancel_futures=True)`. Do not add timeout retries or accept the late value.

- [ ] **Step 4: Verify GREEN and adapter regression**

Run the new test and all `tests/agent/test_tool_adapters.py`. Expected: all pass with no unawaited or dangling-test warnings.

- [ ] **Step 5: Commit**

```powershell
git add -- src/agent/tooling/adapters.py tests/agent/test_tool_adapters.py
git commit -m "fix: enforce agent tool adapter deadlines"
```

### Task 3: Execute async model routing safely from WebSocket handlers

**Files:**
- Modify: `tests/agent/test_routing_hybrid.py`
- Modify: `tests/agent/test_chat_handler_agent_events.py`
- Modify: `src/agent/routing/hybrid.py:48-245,423-456`
- Modify: `src/agent/router.py:53-57`
- Modify: `src/web/chat_handler.py:185-201`

- [ ] **Step 1: Write failing request-local model test**

Add a test with two model stubs and concurrent `asyncio.to_thread(router.decide, ...)` calls. Each decision must reflect its own model response, and neither call may mutate `router.hybrid_router.llm`.

- [ ] **Step 2: Write failing WebSocket-loop test**

Use the existing ChatHandler WebSocket stub and an async model whose `generate()` increments a counter and returns a valid arbitration JSON. Run `handle_message()` with `asyncio.run`; assert the model was called once, the LLM-selected skill is used, and no `coroutine was never awaited` warning appears.

- [ ] **Step 3: Verify RED**

Run both named tests. Expected: current code either leaves the model call count at zero in an active loop or races through shared `hybrid_router.llm`.

- [ ] **Step 4: Make model selection request-local**

Change `HybridSkillRouter.decide` to accept `llm=None`, compute `effective_llm = llm if llm is not None else self.llm`, and pass it into `_llm_arbitrate(query, candidates, effective_llm)`. Change `_llm_arbitrate` to call that argument. Remove the assignment `self.hybrid_router.llm = llm` from `SkillRouter.decide`.

- [ ] **Step 5: Move synchronous arbitration off the WebSocket loop**

In ChatHandler, replace the direct call with:

```python
route_decision = await asyncio.to_thread(
    skill_router.decide,
    message,
    getattr(self.agent_system, "llm", None),
)
```

Preserve the current clarification and `active_skill` flow.

- [ ] **Step 6: Record model fallback reason**

Return arbitration failure metadata from the router as a decision reason such as `llm_arbitration_unavailable`; do not include exception text or credentials. The scoring decision remains valid.

- [ ] **Step 7: Verify GREEN**

Run the two new tests, all routing tests, and `tests/agent/test_chat_handler_agent_events.py`.

- [ ] **Step 8: Commit**

```powershell
git add -- src/agent/routing/hybrid.py src/agent/router.py src/web/chat_handler.py tests/agent/test_routing_hybrid.py tests/agent/test_chat_handler_agent_events.py
git commit -m "fix: await request-local agent route arbitration"
```

### Task 4: Prevent replay status promotion and report strict rates

**Files:**
- Modify: `tests/agent/test_evaluation_runner.py`
- Modify: `tests/agent/test_real_acceptance_checks.py`
- Modify: `src/agent/evaluation/scientific.py:88-159,708-739`

- [ ] **Step 1: Add failing replay tests**

Create separate cases for original `status=failed`, skill mismatch, missing expected tool, forbidden tool, and missing fields needed to validate a tool case. Assert no case becomes passed and each reason is explicit.

- [ ] **Step 2: Add failing rate tests**

For results `[passed, partial, failed, skipped]`, require each strict rate to equal 0.25, `completion_rate` to equal 0.50, and deprecated-compatible `pass_rate` to equal `completion_rate`.

- [ ] **Step 3: Verify RED**

Run the new tests. Expected: original failures and route mismatches are promoted; strict rates are absent.

- [ ] **Step 4: Recompute replay contracts**

Update `_replay_case_status` to preserve original failed, compare expected/actual skill, compare ordered tool subsequences, reject forbidden tools, and return partial/failed when required fields are absent. Keep replay offline.

- [ ] **Step 5: Add strict status metrics**

Create a shared status-rate helper returning counts and `passed_rate`, `partial_rate`, `failed_rate`, `skipped_rate`, and `completion_rate`. Use it in real stability and replay summaries. Retain `pass_rate` as an alias for `completion_rate` for report compatibility.

- [ ] **Step 6: Verify GREEN**

Run `tests/agent/test_evaluation_runner.py` and `tests/agent/test_real_acceptance_checks.py`. Then run contract acceptance and ensure its schema remains readable.

- [ ] **Step 7: Commit**

```powershell
git add -- src/agent/evaluation/scientific.py tests/agent/test_evaluation_runner.py tests/agent/test_real_acceptance_checks.py
git commit -m "fix: preserve scientific acceptance failures in replay"
```

### Task 5: Run quality-boundary release gates

**Files:** no tracked output files.

- [ ] **Step 1: Run focused suites**

Run ToolAdapter, router, ChatHandler, evaluation, and workflow contract tests. Expected: all pass.

- [ ] **Step 2: Run all Agent tests and compile**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m pytest tests/agent -q -p no:cacheprovider
C:/Users/xkx52/.conda/envs/MedChat/python.exe -m compileall -q src scripts
```

- [ ] **Step 3: Run every Node test**

Enumerate tracked `tests/*.js` files and execute each with Node. Expected: 8/8 pass.

- [ ] **Step 4: Run contract acceptance**

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe scripts/run_agent_acceptance.py --mode contract --output scratch/platform-v1-contract-after-quality-fixes.json
```

Expected: 34/34 passed.

- [ ] **Step 5: Inspect scope**

Run `git diff --check`, credential scan, and `git status --short`. Confirm only plan and implementation commits are present and the original dirty worktree digest remains unchanged.
