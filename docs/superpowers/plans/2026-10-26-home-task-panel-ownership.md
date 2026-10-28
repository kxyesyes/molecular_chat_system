# Home Task Panel Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Preserve per-request task history in the real home chat.

**Architecture:** Decision runtime owns a DOM panel per accepted turn. The renderer accepts explicit fresh creation while retaining default reuse. Transport authority gates and scientific contracts do not change.

**Tech Stack:** Existing native JavaScript, Node VM contract tests, FastAPI/Jinja2, real browser.

## 1. Reproduce in automated tests

- [ ] Add full-runtime tests in `tests/home_decision_runtime_test.js` using `loadHome`, `start`, `accepted`, `result`, `complete` and real `task_panel.js`. Emit `agent_event` frames with matching turn/trace, finish first request, start second and assert:

```js
assert.equal(h.chat.querySelectorAll('.agent-task-panel').length, 2);
assert.equal(first.textContent, firstText);
assert.strictEqual(second.parentNode.children[indexOfSecondUser + 1], second);
```

- [ ] Cover cancelled/completed first turns, same-trace resume, no-event resumed waiting, late events and same-turn provisional terminal recovery. Run `node tests/home_decision_runtime_test.js`; confirm assertions fail on singleton reuse, not fixture errors.

## 2. Minimal runtime/renderer fix

- [ ] `src/web/static/js/home/task_panel.js`: add third options argument `{reuse = true} = {}`; query for existing panels only when reuse is true.
- [ ] `src/web/static/js/home/main.js`: record the just-added user row on `decisionTurn.userMessage`; reset `agentTaskState` only when `request_accepted` passes existing guards. `createAgentTaskPanel` returns `decisionTurn.taskPanel` or creates with `{reuse: false}`, sets `data-turn-id`, and inserts immediately after `userMessage`. Waiting correction uses `turn.taskPanel`. Do not reset an existing decision-turn panel when an internal terminal is followed by further events.
- [ ] Keep legacy behavior covered by `tests/home_agent_task_panel_test.js`, adding `decisionMode: false` to its extracted-function fixture as required.
- [ ] Bump active `main.js` and `task_panel.js` versions to `20261026-turn-panels-v1` in `src/web/templates/index.html`; update exact cache assertions in runtime/settings/reference/tool-toggle tests.
- [ ] Run Node runtime/panel tests and `node --check` for changed JS. Run all `tests/home_*_test.js` plus `tests/frontend_safe_render_test.js`; `python -m compileall -q src/web`; `git diff --check`.

## 3. Review and real acceptance

- [ ] Request independent bounded code review while parent executes real browser tests. Review changed ownership paths, same-trace resume, late-event guards and last-assistant placement.
- [ ] Open a fresh page on the already-running owned local server; keep frontend-configured Agent model and separate generator model unchanged. Submit two property requests and verify panel/result ownership. Submit an incomplete property request, Continue with CCO, cancel a subsequent request, then submit CCN. Verify each historical panel remains unchanged, including after recovery.
- [ ] Record actual results, known limits and git state here. Preserve unrelated dirty files. No commit/PR/merge in this bounded continuation.

## Validation record

Completed 2026-10-26. All implementation, review and real-frontend acceptance items above are complete; checklist items remain as the executable procedure.

- Initial runtime RED: 86 passed / 4 failed, including completed/cancelled history and same-trace resume singleton failures. The same-turn test initially asserted suppressed display prose; corrected it to assert retained DOM row identity and count. A process-local mutation restoring the old reset guard then produced the intended 90 passed / 1 failed, without modifying production files.
- Final runtime GREEN: 91 passed / 0 failed (six added cases, including reconnect isolation). Renderer fresh-create compatibility assertion also passes.
- All 22 `tests/home_*_test.js` plus `tests/frontend_safe_render_test.js` suites (21 home + 1 safe-render) passed. `node --check` passed for both production files and all six changed test files. `python -m compileall -q src/web` passed. `git diff --check` passed with only an existing unrelated controller CRLF warning.
- Independent spec and code-quality reviewers returned PASS; both closed. No remaining findings in the bounded patch.
- Real frontend on the owned local server: fresh page loaded both `20261026-turn-panels-v1` scripts with configured Agent `deepseek-v4-pro`. CCO properties completed (MW 46.07, displayed logP -0.001); independent CCN properties completed (MW 45.08, displayed logP -0.035). Missing-structure request waited, explicit Continue with CCO completed with the same workflow trace and a different turn/panel. Generation request cancelled during planning; subsequent CCN calculation completed. Six panels remained, each after its own user message and before its assistant result. Snapshot equality confirmed every earlier panel unchanged after the sixth request. No scientific response was injected or substituted in browser acceptance.
- Screenshot: `outputs/ui-verification/20261026-turn-panels-after.png` (ignored local output). Temporary taller viewport restored to default; acceptance page retained for inspection.
- UX: correct historical attribution. Developer experience: explicit runtime ownership with backward-compatible renderer creation and regression tests. Agent interaction: cancellation/continuation controls and socket/turn/trace authority unchanged; displayed execution now stays with its originating input.
- No backend/scientific algorithm/API/schema/deployment/data changes in this patch. Full Python/real-service suite and deployment health check not rerun for this frontend-only change; browser scientific calls and focused frontend regression were run instead.
- Branch `codex/feat/pi-baseline-migrate-worktree`, HEAD `95c29f8b`. Nothing staged, no new commit/PR/merge. Existing unrelated dirty files preserved.

Remaining overall-goal work is outside this patch: comparison wording that requests original predictions/consistency checks is still over-rejected; reverse-target data directory is absent and awaits a user-provided asset location. Neither is claimed fixed here.
