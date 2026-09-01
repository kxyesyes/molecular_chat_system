# Chat Greeting and Truncation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure plain chat uses the configured external model, never shows a fake Agent workflow, and reports output truncation explicitly.

**Architecture:** Keep the existing Router/Agent boundary and change only chat orchestration. WebSocket input always enters `_process_message`; model token limits are selected by provider; the frontend creates workflow UI lazily from real Agent events.

**Tech Stack:** Python 3.10+, FastAPI WebSocket, OpenAI-compatible adapter, vanilla JavaScript, pytest, Node.js static tests.

---

### Task 1: Lock backend regressions with tests

**Files:**
- Modify: `tests/agent/test_chat_handler_agent_events.py`

- [ ] Add a WebSocket test proving `你好` invokes the main model and does not emit Agent events.
- [ ] Add provider-aware token-budget tests proving external models receive 4096 and local models retain 600.
- [ ] Add a streaming truncation test proving `finish_reason=length` becomes a visible warning and structured completion metadata.
- [ ] Run the focused tests and verify they fail for the missing behavior.

### Task 2: Lock frontend workflow-panel regression

**Files:**
- Modify: `tests/home_agent_task_panel_test.js`

- [ ] Assert `sendMessage()` does not call `resetAgentTaskPanel()`.
- [ ] Assert `handleAgentEvent()` resets the panel only for the first workflow event.
- [ ] Run the Node test and verify it fails for the current unconditional reset.

### Task 3: Implement backend fixes

**Files:**
- Modify: `src/web/chat_handler.py`
- Modify: `config/ollama_config.yaml`

- [ ] Remove the greeting early-return branch and obsolete static greeting response.
- [ ] Add `_model_max_tokens()` using `external_max_tokens` only for models exposing an external provider.
- [ ] Use the selected budget in stream, fallback, and non-stream generation paths.
- [ ] Add `_finalize_model_response()` to append a truncation warning and return safe completion metadata.
- [ ] Add concise plain-answer guidance to `_build_prompt()`.
- [ ] Run focused pytest tests and verify green.

### Task 4: Implement frontend lazy workflow panel

**Files:**
- Modify: `src/web/static/js/home/main.js`

- [ ] Remove unconditional workflow-panel reset from message submission.
- [ ] Reset on `task_started` or `planning_started` only when a new workflow begins.
- [ ] Run `node --check` and the Node regression test.

### Task 5: Verify and hand off

**Files:**
- No additional production files.

- [ ] Run focused pytest and Node tests.
- [ ] Run `python -m pytest tests/agent -q -p no:cacheprovider`.
- [ ] Run `python -m compileall -q src scripts`.
- [ ] Restart the local service without persisting credentials.
- [ ] Verify greeting and conceptual questions against the configured external model.
- [ ] Stage only task files and commit with `fix: route plain chat through external model`.
