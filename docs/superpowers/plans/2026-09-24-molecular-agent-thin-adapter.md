# MolecularAgent Thin Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task-by-task. Preserve all scientific and resource boundaries.

**Goal:** Remove the legacy MolecularAgent execution loop while retaining its public interface and complete canonical results.

**Architecture:** MolecularAgent owns parameter/shape compatibility only. A request-local Supervisor uses existing tools, Router, Planner, executor and Session; optional tools are resolved only from its plan. No new scientific evaluator or execution runtime.

**Tech Stack:** Python, pytest, existing dataclasses and Agent runtime; isolated temporary config, no external model.

Approved design: [thin adapter](../specs/2026-09-24-molecular-agent-thin-adapter-design.md), including cancelled/rejected preservation. Baseline `5f56053`; design commits `fd8a602`, `812fceb`. User confirmed the written specification on 2026-09-24.

## Files and responsibilities

- Modify `src/agent/agent_executor.py`: replace legacy selection/execution with parameter validation, canonical delegation and legacy shape mapping; retain public signatures.
- Add `tests/agent/test_molecular_agent_adapter.py`: public methods, result mapping, signatures, no duplicate execution, input preflight and optional-load boundaries.
- Add `tests/agent/test_molecular_agent_adapter_science.py`: actual Supervisor/Session/validator integration with synthetic tools; no production assets.
- Modify `tests/agent/test_agent_executor.py`: keep generation/count safeguards; migrate only approved legacy routing expectations and fixtures to valid scientific tool contracts.
- Modify `docs/AGENT_MAINTENANCE.md`, add `docs/handoff/molecular-agent-thin-adapter.md`: current entry point and migration evidence. Update this plan and approved spec status only.

## Task 1 — Record baseline and RED

- [ ] Run existing `tests/agent/test_agent_executor.py` and `tests/test_agent_llm_wiring.py` through isolated runner below.
- [ ] Add public execute/execute_tools tests with canonical and dictionary failed/partial results. Preserve errors, warnings, evidence, artifacts, repeated observations and cancelled/rejected outcomes. Shape expectation:

```python
assert result['success'] is False
assert result['partial'] is True
assert result['status'] == 'partial'
assert [item['tool_name'] for item in result['tool_results']] == ['property_calculator', 'activity_predictor']
assert result['tool_results'][1]['error']['code'] == 'external_tool_unavailable'
```

- [ ] Run new tests before production changes. Distinguish fixture/setup errors from real missing delegation/state behavior.

## Task 2 — Minimal delegation and mapping

- [ ] Retain preflight helpers for exact invalid-count error details; None means omission. Preserve temperature and explicit valid mol_count. Do not call should_use_tools again inside execute (it would reparse authoritative count).
- [ ] Construct request-local Supervisor from core + cached optional tool mapping, passing llm only to its main-model slot. Resolve policy once; no match returns old non-success message, no tool calls.
- [ ] Use the same Supervisor's public plan with resolved skill/count to determine optional requirements. Resolve existing optional factory class names, cache safely, snapshot into the request-local tool map; unknown/missing tools remain canonical failure. Never call a tool to discover its name or suitability.
- [ ] Execute exactly once with resolved active_skill and optional count:

```python
kwargs = {'temperature': temperature, 'active_skill': policy}
if mol_count is not None:
    kwargs['mol_count'] = mol_count
response = supervisor.execute(query, **kwargs)
```

- [ ] For AgentResult-backed responses derive fields from its existing serializer, never from the legacy success-or-partial boolean:

```python
payload = response['agent_result'].to_legacy_dict()
sequence = payload['tool_result_sequence']
payload.update(response=payload['final_answer'] or payload['message'],
               used_tools=[item['tool_name'] for item in sequence],
               tool_results=sequence,
               trace_id=response['trace_id'],
               agent_events=response['agent_events'])
```

- [ ] execute_tools delegates to this same implementation once and renames tool_results to results. Keep old invalid-input shape, add truthful status/partial metadata; no scientific execution indicated for preflight errors.
- [ ] Remove old should_use loops, _should_load_optional_tool keyword dispatch, direct tool.execute and duplicated success-only filtering. Preserve getters and lazy-load helper where still needed.
- [ ] Run mapping + old count tests; repair implementation failures. Update tests only for approved routing changes, preserving count and no-model assertions. Never disable validators to obtain green.

## Task 3 — Scientific and lifecycle regression

- [ ] Actual public entry → Supervisor → Session must reject invalid SMILES, demo activity, incomplete docking, absent target evidence; ordinary chat/explanation must not calculate.
- [ ] Optional factories run only for requested canonical plan tools; no heavy tools for greetings or rejected generation count. Exceptions retain canonical failure; KeyboardInterrupt/cancellation propagation is not swallowed.
- [ ] Valid generated candidates keep count/identity validation and downstream input binding. Test temperature/count forwarding without using actual model.
- [ ] Ensure no closing borrowed tools, no second store/session runtime, no registration mutation during request execution; run existing Session cleanup tests as shared-runtime evidence.
- [ ] Commit precisely scoped code/tests after GREEN; do not stage unrelated files or publish.

## Task 4 — Review and delivery

- [ ] Independent SPEC then QUALITY review; new findings require regression before fixing.
- [ ] Run full Agent plus existing related Web/Session/scientific regression, offline contract with socket blocked, memory compile src/scripts, diff check and scoped secret-candidate scan.
- [ ] Report actual failures, skips, warnings, branches, commits and compatibility changes. No new PR merge, models, assets or deployment.

## Isolated commands

Reuse the already validated temporary-environment runner, not the user config. Run from this worktree:

```powershell
$python='C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
$runner=$m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr','D:/MedChat/molecular_chat_system_worktrees/molecular-agent-thin-adapter')
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_agent_executor.py tests/test_agent_llm_wiring.py
exit $LASTEXITCODE
```

Replace test arguments for new files, then `tests/agent` and related integration paths. Expected RED is lost result fields or direct legacy execution; expected GREEN is all applicable tests passing. Do not claim real scientific acceptance from synthetic fixtures.
