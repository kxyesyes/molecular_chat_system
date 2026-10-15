# Complex Workflow Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Bound allocation waits and preserve truthful, candidate-level scientific results through complex workflows.

**Architecture:** Keep existing event names and observation statuses. Use a narrowly validated partial-activity boundary for downstream evidence, and explicit dependency-blocked metadata for steps that never execute. Allocation deadlines must prevent late dispatch and isolate request progress.

**Tech Stack:** Python/FastAPI/asyncio, existing agent contracts and pytest, native JavaScript and Node tests.

## Task 1: Allocation deadline and progress

Files: `src/web/chat_handler.py`, `src/agent/routing/hybrid.py`, focused `tests/agent/test_allocation_deadline.py`; introduce a small owned allocation helper if needed.

- [x] Add deterministic delayed-model/route tests. Assert heartbeat before deadline, explicit timeout exactly once, no late tool call after releasing the delayed model, prompt cancellation, and isolation between requests.

```python
assert terminal_status == "timeout"
assert len(terminal_messages) == 1
assert tool_calls == []
```

- [x] Run `python -m pytest tests/agent/test_allocation_deadline.py -q` and observe failure.
- [x] Add default `30.0`-second allocation deadline and `5.0`-second progress interval. Reuse owned execution/cancellation mechanisms where available. Do not detach unbounded workers or apply the allocation deadline to running scientific tools.
- [x] Run the new suite and existing chat, router, cancellation and lifecycle tests; review for late side effects.

## Task 2: Equivalent target phrases

Files: `src/agent/contracts/target_request.py`, `tests/agent/test_target_selection_phrase.py`.

- [x] Add suffix-role and whitespace variants plus unknown, multiple, negated and switching guards.

```python
selection = analyze_target_request("以 PDE 为靶点 设计三个候选分子")
assert selection.targets == ("PDE",)
assert not selection.needs_clarification
```

- [x] Run the specific regression and observe the current false unknown target.
- [x] Recognize the bounded `以 <target> 为靶点` role; do not parse its suffix as a new forward label. Keep explicit unknown and qualified constraints.
- [x] Run target phrase, analysis phrase, hybrid router and planner tests.

## Task 3: Reverse-target diagnostics

Files: `src/reverse_target/owned_source.py`, `src/agent/tools/reverse_target_tool.py`, focused reverse-target tests.

- [x] Build invalid receipt/proof/output fixtures and assert safe stage/reason/field diagnostics without raw exception strings or predictions.

```python
assert result["success"] is False
assert result["data"] is None
assert result["error"]["details"]["stage"]
assert result["error"]["details"]["reason"]
```

- [x] Observe failing diagnostic assertions before adding bounded diagnostic exceptions or allowlisted mappings.
- [x] Preserve all existing receipt, digest and provenance rejection checks; use generic safe reason only when no specific reason is known.
- [x] Run reverse-target tool, owned-source and source-proof regressions including secret/path redaction.

## Task 4: Partial observation propagation and dependency non-execution

Files: `src/agent/validators/result_validator.py`, `src/agent/validators/candidate_alignment.py`, `src/agent/contracts/result.py`, `src/agent/runtime/run_session.py`; focused `tests/agent/test_partial_activity_workflow.py` and `tests/agent/test_dependency_blocked_workflow.py`. Add a small partial-activity validator module to share a single eligibility boundary.

- [x] Construct an explicitly synthetic, pinned three-row family batch (one passed, two full-execution consistency conflicts). Test validation, candidate IDs, downstream binding, partial run aggregation and final raw values.

```python
assert result.outcome.value == "partial"
assert not result.success
assert len(downstream_input["outputs"]["activity"]) == 3
assert "4.2" in result.final_answer
```

- [x] Add hostile partial rows (missing provenance, inconsistent flag, demo/fallback, unknown row shape) and assert no downstream publication.
- [x] Observe regressions. Accept only error-free partial `activity_predictor` family batches that pass existing domain validation and contain real numeric observations. Revalidate at trust boundaries rather than trusting a quality flag. Align before binding, keep success false, and include eligible formatted observations in final reports.
- [x] Add upstream-failure/dependent-step test. Assert dependent tool call count zero, no started event, `not_calculated`, reason `dependency_blocked`, and upstream step identity.
- [x] Distinguish unresolved bindings caused by an executed failed producer from malformed/missing selectors. Preserve genuine invalid input errors and upstream error as run cause.
- [x] Emit existing `tool_completed` with truthful payload status for settled partial/blocked steps; do not emit a tool execution failure for those cases. Maintain checkpoint/recovery semantics.
- [x] Run workflow/session/resume/validator/candidate-alignment suites.

## Task 5: Ranking and visible state

Files: `src/agent/tools/candidate_ranker.py`, `src/web/agent_result_presentation.py`, `src/web/static/js/home/task_status.js`, related focused Python/Node tests.

- [x] Test one accepted row and two conflict rows: one activity score used, two excluded with explicit per-candidate reasons; original values preserved. Assert fixed scoring weights and required-evidence checks unchanged.
- [x] Test payload-aware UI labels for partial and `not_calculated` dependency-blocked events and preserve real failure labels.
- [x] Observe failures, then add safe per-row exclusion diagnostics and truthful labels. Backend task 4 owns final-answer raw-value inclusion; this task must not edit `run_session.py`.
- [x] Run candidate-ranker, agent presentation and task-panel tests, and `node --check` for modified JavaScript.

## Task 6: Integration, review and draft PR

- [x] Run focused suites together, `python -m pytest tests/agent -q`, relevant full-suite tests, `python -m compileall -q src scripts`, three Node tests and repository JavaScript syntax checks. Broad-run failures and their passing targeted reruns, plus the baseline C4a hang, are recorded in the verification document.
- [x] Run contract/replay acceptance if available without external credentials; report any environmental limitations honestly. Contract passed 34/34; real replay input was unavailable. No real-model recalibration is part of this change.
- [x] Review spec compliance first, then code quality, and fix actionable findings with regression tests.
- [ ] Explicitly stage only task files; commit Conventional Commit changes on `codex/fix-complex-workflow-status`. Push and create one draft PR targeting main with scope, commands/results, risks and review focus. Attach the PR. Do not merge.

## Execution boundaries

The parent owns task 4 and integration. Independent workers own tasks 1, 2, 3 and 5 with disjoint write sets. Workers edit directly in the isolated worktree and do not commit, push, or change branches; the parent serializes publication. User approved parallel implementation and the written specification. All test predictions are fixtures and must not be reported as real model validation.
