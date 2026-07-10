# MedChat Agent Runtime Real Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the runtime, workflow-dataflow, permission, validation, event-stream, and answer-drift issues identified in the June 19 architecture review, then execute REAL-001 through REAL-016 with real scientific dependencies wherever available.

**Architecture:** Keep the current native MedChat runtime and converge workflow execution on `WorkflowOrchestrator` through a small `WorkflowExecutor` boundary. Make `TaskPlanner` the plan source, bind step outputs through explicit workflow state, enforce tool allowlists before execution, preserve the complete `ToolResult` contract, validate domain claims against evidence, and stream events during execution instead of replaying them only after completion.

**Tech Stack:** Python 3.10, FastAPI, WebSocket, pytest, SQLite, RDKit, local Ollama `gmm-llama:latest`, RG-MPNN, AutoDock Vina, OpenAI-compatible external model.

---

## File map

- `src/agent/orchestrators/base.py`: workflow step and state contracts.
- `src/agent/orchestrators/workflow.py`: single workflow execution engine, data binding, timeout, required/optional behavior, and events.
- `src/agent/runtime/workflow_executor.py`: routed skill, plan, permissions, and orchestrator composition.
- `src/agent/react_agent.py`: workflow facade; open-ended ReAct remains fallback only.
- `src/agent/supervisor.py`: compatibility facade over the same executor.
- `src/agent/planning/task_planner.py`: sole deterministic workflow-plan producer.
- `src/agent/tools/base_tool.py`: complete legacy-to-`ToolResult` normalization.
- `src/agent/validators/`: docking, activity, ADMET, target-evidence, and aggregate validators.
- `src/web/chat_handler.py`: one-time routing, live event forwarding, and authoritative workflow-result output.
- `data/agent_evals/real_agent_cases.jsonl`: REAL-001 through REAL-016 definitions.
- `scripts/run_agent_acceptance.py`: contract and controlled real-case execution with redacted artifacts.
- `tests/agent/`: regression tests for every architecture issue and real-case contract.

### Task 1: Freeze the June 19 defect matrix

**Files:**
- Create: `tests/agent/test_june19_architecture_contract.py`
- Modify: `data/agent_evals/real_agent_cases.jsonl`

- [ ] Add tests that prove the current adapter loses structured fields, required failures can continue incorrectly, timeout is not enforced, an unauthorized planned tool is not rejected, and workflow output is passed to a second model by default.
- [ ] Run the focused file and confirm each new assertion fails for the intended behavior.
- [ ] Encode REAL-001 through REAL-016 with expected skill, ordered tools, forbidden claims, dependency policy, and event requirements.

### Task 2: Preserve the complete ToolResult contract

**Files:**
- Modify: `src/agent/tools/base_tool.py`
- Modify: `src/agent/contracts/result.py`
- Test: `tests/agent/test_tool_adapter_compat.py`
- Test: `tests/agent/test_june19_architecture_contract.py`

- [ ] Normalize `warnings`, `evidence`, `artifacts`, and `quality` in successful legacy dictionaries.
- [ ] Preserve structured fields and the original error code/details in failed legacy dictionaries.
- [ ] Accept artifact dictionaries by converting them to `WorkflowArtifact`.
- [ ] Run focused adapter tests and confirm complete round-trip preservation.

### Task 3: Make workflow execution a real stateful dataflow

**Files:**
- Modify: `src/agent/orchestrators/base.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Test: `tests/agent/test_workflow_orchestrator.py`
- Test: `tests/agent/test_workflow_resume.py`
- Test: `tests/agent/test_june19_architecture_contract.py`

- [ ] Add a `WorkflowState` contract containing inputs, outputs, artifacts, warnings, and errors.
- [ ] Resolve `input_from` without flattening arbitrary structured values; render templates recursively for strings, dictionaries, and lists.
- [ ] Enforce `required=True` as a stop condition even when the workflow default permits optional failures.
- [ ] Enforce `timeout_seconds` at the execution boundary and return `TOOL_TIMEOUT`.
- [ ] Store outputs, artifacts, warnings, and errors in the state and expose a redacted state summary in `AgentResult.metadata`.
- [ ] Verify generated candidates become actual downstream inputs in target-driven and hit-to-lead plans.

### Task 4: Converge workflow entry points and enforce permissions

**Files:**
- Create: `src/agent/runtime/workflow_executor.py`
- Modify: `src/agent/runtime/__init__.py`
- Modify: `src/agent/react_agent.py`
- Modify: `src/agent/supervisor.py`
- Modify: `src/agent/router.py`
- Test: `tests/agent/test_workflow_executor.py`
- Test: `tests/agent/test_supervisor_agent.py`
- Test: `tests/agent/test_react_agent_workflow_routing.py`

- [ ] Add `WorkflowExecutor.execute(context, skill, all_tools)` as the single plan-and-run path.
- [ ] Filter tools with the routed skill allowlist before execution.
- [ ] Reject plans containing a missing or unauthorized tool as a configuration error before any tool runs.
- [ ] Route ReAct workflow skills and Supervisor compatibility calls through the same executor.
- [ ] Preserve existing legacy response shapes while adding plan and state metadata.

### Task 5: Remove plan drift

**Files:**
- Modify: `src/agent/planning/task_planner.py`
- Modify: workflow skill declarations under `src/agent/skills/`
- Test: `tests/agent/test_task_planner.py`
- Test: `tests/agent/test_workflow_skills.py`

- [ ] Ensure skills declare intent and allowed tools but do not independently define executable step order.
- [ ] Add missing drug-likeness steps required by REAL-001, REAL-002, and REAL-003.
- [ ] Preserve target search before generation for REAL-005.
- [ ] Diagnose baseline properties, ADMET, and activity before generation in REAL-006.
- [ ] Add plan-fidelity assertions comparing planned and executed tool order.

### Task 6: Add evidence-bound scientific validators

**Files:**
- Create: `src/agent/validators/domain_validators.py`
- Modify: `src/agent/validators/result_validator.py`
- Modify: `src/agent/validators/__init__.py`
- Test: `tests/agent/test_domain_result_validators.py`
- Test: `tests/agent/test_input_validators.py`

- [ ] Reject docking energy claims unless receptor, ligand, box, pose, and numeric energy evidence exist.
- [ ] Reject activity or pIC50 claims without successful model provenance.
- [ ] Reject numeric ADMET claims without a successful ADMET tool result and provenance.
- [ ] Reject target and PDB claims without target-search or reverse-target evidence.
- [ ] Convert rejected claims into failed or partial results with explicit validation warnings; never fabricate replacements.

### Task 7: Stream events live and prevent answer drift

**Files:**
- Modify: `src/agent/runtime/event_bus.py`
- Modify: `src/web/chat_handler.py`
- Test: `tests/agent/test_chat_handler_agent_events.py`
- Create: `tests/agent/test_chat_handler_authoritative_results.py`

- [ ] Attach a thread-safe callback at execution time and forward events to the WebSocket as they are emitted.
- [ ] Preserve event order: task started, planning completed, tool started/completed or failed, task completed or failed.
- [ ] Route once in `ChatHandler` and pass the selected skill into execution.
- [ ] For workflow results, send the authoritative `final_answer` directly by default.
- [ ] Keep optional model summarization behind an explicit request/config flag and prohibit changes to tool-provided facts.

### Task 8: Upgrade the acceptance harness to REAL-001 through REAL-016

**Files:**
- Modify: `scripts/run_agent_acceptance.py`
- Modify: `src/agent/evaluation/models.py`
- Modify: `src/agent/evaluation/runner.py`
- Test: `tests/agent/test_prompt_acceptance.py`
- Test: `tests/agent/test_real_acceptance_checks.py`

- [ ] Load the 16-case dataset and record expected/actual skill, tools, events, warnings, hallucination checks, status, reason, and fix notes.
- [ ] Separate deterministic contract execution from controlled real integration.
- [ ] Require RDKit-valid unique requested-count SMILES from local `gmm-llama:latest`.
- [ ] Require real RG-MPNN model provenance for activity success.
- [ ] Require actual docking poses and numeric binding energy for docking success.
- [ ] Treat unavailable external dependencies as explicit `skipped` or `not_executable`, never as fabricated success.
- [ ] Redact credentials recursively from reports and errors.

### Task 9: Execute real validation and repair regressions

**Files:**
- Generate: `outputs/agent_evaluation/agent_real_cases_20260619.json`
- Generate: `outputs/agent_evaluation/agent_contract_cases_20260619.json`
- Generate: `outputs/agent_evaluation/agent_validation_summary_20260619.md`

- [ ] Run the focused tests after each change.
- [ ] Run all `tests/agent`.
- [ ] Run full `tests/` using the MedChat Conda environment.
- [ ] Run strict health checks, `compileall`, and JavaScript syntax checks.
- [ ] Run REAL-001 through REAL-016 in contract mode.
- [ ] Run real external main-model connectivity if a transient environment key is available.
- [ ] Run local Ollama generation, target search, RG-MPNN inference, and docking execution.
- [ ] Fix every reproducible project defect, rerun the affected case, and record before/after evidence.
- [ ] Scan workspace text and generated artifacts for credential-shaped content.

### Task 10: Final review

**Files:**
- Modify: `docs/handoff/latest.md`
- Review: all files changed during this task.

- [ ] Confirm every P0/P1 issue maps to a passing test or an explicit dependency skip.
- [ ] Confirm REAL-001 through REAL-016 each has a final status and evidence.
- [ ] Confirm no existing user-owned change was reverted.
- [ ] Confirm the molecular generator still uses local Ollama `gmm-llama:latest`.
- [ ] Confirm no secret is committed, persisted, or printed.
- [ ] Record exact commands, pass counts, artifact paths, unresolved external limitations, and follow-up recommendations.
