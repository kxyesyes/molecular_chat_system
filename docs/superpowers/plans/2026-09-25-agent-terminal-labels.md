# Agent terminal labels implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Task terminal text must distinguish success, partial, failure, rejection and cancellation without changing backend outcomes.

**Architecture:** Retain existing presentation resolver and terminal set. Replace substring completion matching with an exact task-terminal lookup; intermediate events keep percentage/executing text. No new state machine or panel structure.

**Tech Stack:** Native JavaScript, Node assert/vm tests, Jinja homepage template.

Approved design: `docs/superpowers/specs/2026-09-25-agent-terminal-labels-design.md` (21e4e66). User explicitly approved entering TDD.

## Task 1: Reproduce exact terminal/intermediate presentation

Files: `tests/home_agent_task_panel_test.js`, `tests/home_workflow_completion_behavior_test.js`.

- [x] Extend the existing extracted resolver tests before changing production code:

```javascript
const labels = {
  task_completed: "已完成", task_partial: "部分完成", task_failed: "失败",
  task_rejected: "已拒绝", task_cancelled: "已取消",
};
for (const [event, label] of Object.entries(labels)) {
  for (const progress of [undefined, 0.4, 1]) {
    const result = resolveAgentEventPresentation({ event, progress });
    assert.strictEqual(result.progressText, label);
    assert.strictEqual(result.terminal, true);
  }
}
for (const event of ["planning_completed", "tool_completed"]) {
  assert.strictEqual(resolveAgentEventPresentation({ event }).progressText, "执行中");
  assert.strictEqual(resolveAgentEventPresentation({ event, progress: 0.4 }).progressText, "40%");
  assert.strictEqual(resolveAgentEventPresentation({ event }).terminal, false);
}
const getAgentEventLabel = vm.runInNewContext(`(${extractFunction("getAgentEventLabel")})`);
for (const event of ["task_partial", "task_rejected", "task_cancelled"]) {
  assert.strictEqual(getAgentEventLabel(event), labels[event]);
}
```

- [x] Replace only the old partial progress expectation `100%` with `部分完成` and its assertion explanation. Keep warning class, terminal and no-success-style assertions.
- [x] Run both Node scripts individually; preserve RED mismatch output. Expected old resolver fails intermediate and partial semantics, not missing imports/syntax.

## Task 2: Minimal fix and cache version

Files: `src/web/static/js/home/main.js`, `src/web/templates/index.html`, `tests/home_scientific_references_test.js`, and exact-version assertion in `tests/home_workflow_completion_behavior_test.js`.

- [x] In resolver replace substring matching with a local exact map using the five entries above:

```javascript
const progressText = terminalLabels[eventType] ||
  (percent !== null ? `${percent}%` : "执行中");
```

Use own-entry lookup so unknown inherited object property names do not become labels. Preserve canonical event precedence, terminal set, escaping and classes.
- [x] Add three missing label entries to existing event-label map; do not alter tool-name lookup.
- [x] Change both existing cache expectations to `20260925-terminal-labels-v1`, run scripts to observe stale template failure, then change only homepage main.js query version to match.
- [x] Run:

```powershell
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/home_scientific_references_test.js
node tests/home_structured_molecule_render_test.js
node tests/frontend_safe_render_test.js
node --check src/web/static/js/home/main.js
```

All must exit 0. Run isolated Python `tests/test_static_placeholder_cleanup.py` using the documented integration wrapper; do not read user configuration.

## Task 3: Verify and deliver locally

- [x] Start isolated browser lab with in-memory unavailable likeness injection, real RDKit property tool and no model API; submit CCO property request. Verify task badge and event say 部分完成, successful values and explicit failure remain.
- [x] Independently review spec compliance then quality. Record any failures and corrections.
- [x] Update integration handoff/latest with final file scope, actual commands/counts and remaining limitations. Stop owned temporary services/tabs; do not touch user port 6001.
- [x] Explicitly stage only reviewed task paths and commit `fix: distinguish agent task terminal labels`. No push, PR, merge, deployment or external model activation.
