# DeepSeek Routing and Response Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure conceptual questions reach the external main model, scientific property requests require usable molecular input, and DeepSeek V4 never completes with an unexplained empty answer.

**Architecture:** Keep the existing Router → Supervisor/tool workflow → ChatHandler design. Tighten route intent predicates, add a chat-boundary clarification response for scientific requests that require SMILES, and make the OpenAI-compatible adapter provider-aware without exposing reasoning content.

**Tech Stack:** Python 3.10+, FastAPI/WebSocket, httpx SSE, pytest/unittest.

---

### Task 1: Correct routing intent boundaries

**Files:**
- Modify: `src/agent/routing/hybrid.py`
- Test: `tests/agent/test_hybrid_router.py`

- [ ] Add failing tests asserting that `什么是药物设计` abstains, `针对 PDE5A 设计 10 个候选分子` still routes to target-driven design, and `计算阿司匹林的分子性质` routes to property assessment with confirmation required.
- [ ] Run `python -m pytest tests/agent/test_hybrid_router.py -q -p no:cacheprovider` and confirm the new assertions fail for the current keyword scoring.
- [ ] Replace the broad `"设计"` generation predicate with an explicit generation-intent helper and add common property phrases such as `分子性质` and `理化性质`.
- [ ] Re-run the focused router tests and confirm they pass.

### Task 2: Clarify missing molecular input before retrieval or generation

**Files:**
- Modify: `src/web/chat_handler.py`
- Test: `tests/agent/test_chat_handler_agent_events.py`

- [ ] Add a failing WebSocket-level test showing that a routed property request without SMILES returns a deterministic clarification, does not call RAG, and does not call the external model.
- [ ] Run the focused chat-handler test and confirm it fails because the request currently falls through to legacy RAG/model generation.
- [ ] Add a small preflight result derived from the router decision; emit a normal completed response asking for SMILES when `requires_confirmation` contains the molecular-input requirement.
- [ ] Re-run the focused chat-handler tests and confirm they pass.

### Task 3: Make DeepSeek V4 output handling explicit

**Files:**
- Modify: `src/agent/openai_compatible_model.py`
- Test: `tests/test_openai_compatible_model.py`

- [ ] Add failing tests that DeepSeek V4 payloads disable thinking for the ordinary chat adapter, SSE completion metadata is retained, and a 200 response with no final content returns a clear model error instead of an empty string.
- [ ] Run `python -m pytest tests/test_openai_compatible_model.py -q -p no:cacheprovider` and confirm the new tests fail.
- [ ] Add provider/model capability detection, send `thinking.type=disabled` for DeepSeek V4 chat calls, track `finish_reason`, and emit a safe explicit error when no final content is received.
- [ ] Re-run the adapter tests and confirm they pass.

### Task 4: Regression and live verification

**Files:**
- Verify only: `src/agent/routing/hybrid.py`, `src/web/chat_handler.py`, `src/agent/openai_compatible_model.py`

- [ ] Run the focused tests for all three components.
- [ ] Run `python -m pytest tests/agent -q -p no:cacheprovider` and `python -m compileall -q src`.
- [ ] Restart MedChat with the existing runtime-only administrator token.
- [ ] Verify `什么是药物设计` calls DeepSeek without molecular generation, `计算阿司匹林的分子性质` requests SMILES without RAG, and `你是什么模型` returns non-empty text.
- [ ] Report exact statuses and preserve the unrelated generated FAISS manifest without staging or deleting it.
