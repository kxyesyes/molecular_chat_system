# MedChat Agent Platform Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build durable Agent state, curated memory, typed tool adapters, centralized specialist delegation, explainable routing, and versioned evaluation while preserving existing MedChat entry points and events.

**Architecture:** Extend the current native `SupervisorAgent`, `WorkflowOrchestrator`, `SkillRegistry`, and tool classes through additive interfaces. SQLite stores normalized execution state; adapters wrap legacy tools; the Supervisor delegates typed tasks to specialist agents; a hybrid router produces scored decisions; the evaluation runner executes deterministic, replay, and controlled real cases.

**Tech Stack:** Python 3.10, FastAPI, Pydantic 2, SQLite, pytest, RDKit, httpx, local Ollama, OpenAI-compatible HTTP API.

---

## File map

- `src/agent/persistence/`: storage interfaces, recursive redaction, SQLite implementation.
- `src/agent/tooling/`: typed specifications, adapters, registry, compatibility bridge.
- `src/agent/specialists/`: typed specialist-agent contracts and domain agents.
- `src/agent/routing/`: ranked candidates, explainable route decisions, LLM arbitration.
- `src/agent/evaluation/`: JSONL datasets, evaluators, reports.
- `data/agent_evals/`: versioned acceptance cases derived from the supplied DOCX.
- `tests/agent/`: unit, integration, recovery, routing, and acceptance tests.
- `scripts/run_agent_acceptance.py`: deterministic and optional real-integration runner.

### Task 1: Durable persistence and redaction

**Files:**
- Create: `src/agent/persistence/base.py`
- Create: `src/agent/persistence/redaction.py`
- Create: `src/agent/persistence/sqlite_store.py`
- Create: `src/agent/persistence/__init__.py`
- Test: `tests/agent/test_agent_persistence.py`

- [ ] **Step 1: Write failing persistence tests**

Cover schema creation, run/checkpoint round trips, ordered events, curated-memory admission, secret redaction, routing feedback, and artifact path metadata.

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_agent_persistence.py -q
```

Expected: import failure because `src.agent.persistence` source modules do not exist.

- [ ] **Step 3: Implement storage contracts and SQLite store**

Implement:

```python
class AgentStateStore(Protocol):
    def start_run(self, run: dict[str, Any]) -> None: ...
    def save_checkpoint(self, checkpoint: dict[str, Any]) -> None: ...
    def latest_checkpoint(self, trace_id: str) -> dict[str, Any] | None: ...
    def append_event(self, event: dict[str, Any]) -> int: ...
    def record_tool_execution(self, execution: dict[str, Any]) -> None: ...
    def add_memory(self, memory: dict[str, Any]) -> str: ...
    def search_memories(self, **filters: Any) -> list[dict[str, Any]]: ...
```

Use parameterized SQL, WAL mode, explicit transactions, JSON serialization, and the tables named in the approved specification.

- [ ] **Step 4: Verify GREEN**

Run the focused test and expect all persistence tests to pass.

### Task 2: Event persistence, checkpoint resume, and idempotency

**Files:**
- Modify: `src/agent/runtime/event_bus.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Modify: `src/agent/orchestrators/base.py`
- Test: `tests/agent/test_workflow_resume.py`
- Test: `tests/agent/test_agent_event_stream.py`

- [ ] **Step 1: Write failing resume tests**

Verify that completed compatible steps are reused after a new orchestrator instance is created, changed input hashes invalidate reuse, duplicate idempotency keys do not repeat tools, and persisted event sequence remains ordered.

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_resume.py tests\agent\test_agent_event_stream.py -q
```

Expected: failures for missing checkpoint-store integration.

- [ ] **Step 3: Add durable orchestration**

Add optional `state_store`, `workflow_version`, and `idempotency_key` boundaries. Hash normalized inputs plus tool/model/adapter versions, persist before and after execution, and reuse only succeeded compatible results.

- [ ] **Step 4: Verify GREEN and regressions**

Run the focused tests plus `tests/agent/test_workflow_orchestrator.py`.

### Task 3: Unified tool specifications, adapters, and registry

**Files:**
- Create: `src/agent/tooling/spec.py`
- Create: `src/agent/tooling/adapters.py`
- Create: `src/agent/tooling/registry.py`
- Create: `src/agent/tooling/__init__.py`
- Modify: `src/agent/tools/base_tool.py`
- Test: `tests/agent/test_tool_adapters.py`
- Test: `tests/agent/test_tool_registry.py`
- Modify: `tests/agent/test_tool_adapter_compat.py`

- [ ] **Step 1: Write failing adapter and registry tests**

Cover Pydantic input/output validation, legacy normalization, timeout and retry policy, sensitive-field redaction, aliases, capability queries, ownership checks, unhealthy-tool rejection, and MCP interface reservation.

- [ ] **Step 2: Verify RED**

Run the three tooling test files and expect import failures.

- [ ] **Step 3: Implement minimal adapters**

Implement `ToolSpec`, `ToolAdapter`, `LegacyPythonToolAdapter`, `HTTPToolAdapter`, `ModelToolAdapter`, `CompositeToolAdapter`, `MCPToolAdapter`, and `ToolRegistry`. Keep `execute_tool_compat` as the legacy bridge.

- [ ] **Step 4: Verify GREEN**

Run tooling tests and all existing tool compatibility tests.

### Task 4: Typed specialist agents and centralized Supervisor delegation

**Files:**
- Create: `src/agent/specialists/contracts.py`
- Create: `src/agent/specialists/base.py`
- Create: `src/agent/specialists/agents.py`
- Create: `src/agent/specialists/__init__.py`
- Modify: `src/agent/supervisor.py`
- Test: `tests/agent/test_specialist_agents.py`
- Test: `tests/agent/test_supervisor_delegation.py`
- Modify: `tests/agent/test_supervisor_agent.py`

- [ ] **Step 1: Write failing specialist tests**

Verify `AgentTask`/`AgentTaskResult`, per-agent allowlists, Molecular Design Agent ownership of `llm_molecular_generator`, read-only Report Agent behavior, and Supervisor-only delegation.

- [ ] **Step 2: Verify RED**

Run specialist and Supervisor tests and expect missing-module or missing-delegation failures.

- [ ] **Step 3: Implement specialists and delegation**

Add Target, Molecular Design, Property/ADMET, Activity, Docking, and Report agents. The Supervisor converts workflow steps into typed tasks, rejects unauthorized tools, delegates sequentially through dependencies, and persists task outcomes.

- [ ] **Step 4: Verify GREEN**

Run specialist, Supervisor, planner, and workflow tests.

### Task 5: Explainable hybrid routing and memory retrieval

**Files:**
- Create: `src/agent/routing/models.py`
- Create: `src/agent/routing/hybrid.py`
- Create: `src/agent/routing/__init__.py`
- Modify: `src/agent/router.py`
- Modify: `src/agent/contracts/context.py`
- Modify: `src/agent/skills/skill_registry.py`
- Test: `tests/agent/test_hybrid_router.py`
- Test: `tests/agent/test_routing_prompt_matrix.py`

- [ ] **Step 1: Write failing route-decision tests**

Use the supplied DOCX route matrix. Assert Top-1 skill, Top-3 inclusion, general-chat abstention, missing-input confirmation, generation-only boundaries, and deterministic handling of explicit entities.

- [ ] **Step 2: Verify RED**

Run both routing tests and expect missing route-decision behavior.

- [ ] **Step 3: Implement routing**

Produce `RouteCandidate` and `RouteDecision`, entity-aware scoring, contradiction rules, tool-availability weighting, bounded memory hints, feedback adjustments, optional structured LLM arbitration, and safe fallback. Preserve `SkillRouter.route()` by returning the selected legacy `BaseSkill`.

- [ ] **Step 4: Verify GREEN**

Run routing tests plus existing ReAct routing tests.

### Task 6: Versioned evaluation datasets and runner

**Files:**
- Create: `src/agent/evaluation/models.py`
- Create: `src/agent/evaluation/runner.py`
- Create: `src/agent/evaluation/__init__.py`
- Create: `data/agent_evals/routing_cases.jsonl`
- Create: `data/agent_evals/workflow_cases.jsonl`
- Create: `data/agent_evals/failure_recovery_cases.jsonl`
- Create: `data/agent_evals/chemistry_quality_cases.jsonl`
- Test: `tests/agent/test_evaluation_runner.py`

- [ ] **Step 1: Write failing evaluator tests**

Verify dataset loading, Top-1/Top-3 metrics, tool selection, ordered workflow steps, abstention, hallucination penalties, SMILES validity/uniqueness/count, P50/P95 latency, and JSON report serialization.

- [ ] **Step 2: Verify RED**

Run evaluator tests and expect missing evaluation modules.

- [ ] **Step 3: Implement evaluator and datasets**

Encode all DOCX route, workflow, hallucination, event, and scoring cases as versioned JSONL. Produce per-case scores out of 10 and aggregate metrics.

- [ ] **Step 4: Verify GREEN**

Run evaluation tests and a deterministic evaluation CLI smoke test.

### Task 7: Full DOCX acceptance harness and security gates

**Files:**
- Create: `scripts/run_agent_acceptance.py`
- Create: `tests/agent/test_prompt_acceptance.py`
- Modify: `.env.example`
- Modify: `scripts/health_check.py`

- [ ] **Step 1: Write failing acceptance tests**

Cover WF-001 through WF-003, HF-001 through HF-005, the required event sequence, legacy event payload shape, no invented docking energy, and model isolation.

- [ ] **Step 2: Verify RED**

Run acceptance tests and record exact failures before implementation changes.

- [ ] **Step 3: Implement validation and acceptance runner**

The runner supports `--mode contract`, `--mode replay`, and `--mode real`. Real mode reads secrets only from environment variables, checks local Ollama `gmm-llama:latest`, records redacted provider/model/latency metadata, and never writes credentials.

- [ ] **Step 4: Verify GREEN**

Run all acceptance tests and generate `outputs/agent_evaluation/agent_acceptance_report.json`.

### Task 8: Complete regression and review

**Files:**
- Review all changed Agent files and generated evaluation output.

- [ ] **Step 1: Run focused Agent suite**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q
```

- [ ] **Step 2: Run full Python suite**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests -q
```

- [ ] **Step 3: Run JavaScript syntax/tests**

Run `node --check` for modified JavaScript and existing Node test scripts.

- [ ] **Step 4: Run real controlled integrations**

Verify external main-model connectivity when a secret is available in the environment, local Ollama generation with `gmm-llama:latest`, target search, RG-MPNN availability, and docking precondition handling.

- [ ] **Step 5: Security scan**

Search tracked and untracked text files for credential-shaped strings and verify persisted/evaluation databases contain only redacted data.

- [ ] **Step 6: Final review**

Check specification coverage, unchanged legacy contracts, exact test counts, generated artifacts, and `git status --short`. Do not revert or discard unrelated changes.
