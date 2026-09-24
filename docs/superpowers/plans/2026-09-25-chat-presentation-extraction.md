# Chat presentation extraction implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development; SPEC then QUALITY review per completed implementation. Preserve TDD evidence.

**Goal:** Finish work package 2 with two stateless calculation modules and thin compatible ChatHandler methods.

**Architecture:** Extract current result-projection algorithms to `agent_result_presentation.py`, prompt/RAG assembly to `chat_prompt_builder.py`. Reuse existing redaction, prompt budget and RAG formatter functions. Inject compatibility callbacks instead of importing the handler from extracted modules.

**Tech Stack:** Python 3.10, existing pytest/async fixtures, unchanged FastAPI/WebSocket.

Approved design: `docs/superpowers/specs/2026-09-25-chat-presentation-extraction-design.md`. User selected this design and delegated subsequent recommended choices; no further choice gate is needed. Baseline 271 passed, 4 existing warnings.

## Task 1: Characterization and failing boundary tests

Files: create `tests/agent/test_chat_presentation_boundary.py`; extend existing tests only when their fixture or injection path must be referenced. Do not delete or weaken existing assertions.

- [ ] Capture exact current helper outputs for representative safe/partial/failure/invalid-status cases before moving code. Include scientific whitespace/precision/SMILES, warnings, skipped typed metadata, malformed mappings and input non-mutation. Run against baseline to establish expected outputs.
- [ ] Add new-boundary tests using actual imports inside test functions. Initial RED must demonstrate absent module/delegation, not a malformed expectation.

```python
def test_presentation_boundary_is_independent():
    import importlib
    module = importlib.import_module('src.web.agent_result_presentation')
    assert module.presentation_status({'success': True, 'status': 'partial'}) == 'partial'
    assert module.presentation_status({'success': True, 'status': 'invalid'}) == 'failed'
```

- [ ] Lock compatibility dynamic dispatch with a ChatHandler subclass overriding `_sanitize_agent_failure_text`, `_sanitize_agent_warnings` and `_input_limit` separately. Verify same hook calls through warnings, partial, failure and prompts. Check malformed-step log message/count/order remains caller-owned, including later exception.
- [ ] Preserve existing monkeypatch of `chat_handler.format_rag_context` and verify oversized/unserializable records never reach formatter.
- [ ] Add actual caller delegation spies (unique sentinel return) for each extracted wrapper; include explicit empty conversation history versus fallback self history and effective limit evaluation order.

Run the documented isolated runner with `tests/agent/test_chat_presentation_boundary.py` and the six baseline files; record failure/pass counts without hiding prior failures. Tests must not read production configuration or call models.

## Task 2: Result presentation module and wrappers

Files: create `src/web/agent_result_presentation.py`; edit `src/web/chat_handler.py`.

- [ ] Move one authoritative implementation for failure envelope, failure text sanitization, warning sanitization, presentation status, partial projection and failure content. Suggested public names: `failure_envelope`, `sanitize_failure_text`, `sanitize_warnings`, `presentation_status`, `partial_projection`, `failure_content`.
- [ ] Keep helper constants authoritative in the new module and import aliases for existing handler compatibility where needed. Keep event-only limits/constants in ChatHandler.
- [ ] Preserve nested callbacks using explicit keyword hooks. Example wrapper shape:

```python
@classmethod
def _agent_failure_content(cls, agent_result):
    return result_presentation.failure_content(
        agent_result, sanitize_text=cls._sanitize_agent_failure_text,
    )
```

`partial_projection` accepts the current text/warning sanitizers and a caller-owned malformed-observation notifier; invoke the notifier at the existing warning point, not after the projection returns. New module does not import logging or ChatHandler. Missing notifier defaults to a no-op, not a shared mutable collection. Preserve the original exception catch scope.
- [ ] Keep all async sends, event/key/candidate projections, history and `_process_message` orchestration in place. Do not change JSON serialization, routing or scientific status to make tests pass.
- [ ] Run result/partial/events/scientific-reference focused regression; all previous and new cases must pass.

## Task 3: Prompt assembly module and wrappers

Files: create `src/web/chat_prompt_builder.py`; edit the same handler after Task 2.

- [ ] Move budgeted RAG assembly, ordinary prompt body and Agent-evidence prompt body. Suggested names: `format_budgeted_rag_context`, `build_chat_prompt`, `build_agent_prompt`.
- [ ] Receive config, selected history and source data explicitly. Accept current formatter and a lazy `input_limit` callable where required for compatibility, rather than a handler/model instance. Evaluate the callable where old code called `_input_limit()`.
- [ ] Preserve legacy signatures and unused `retrieved_molecules` arguments at the handler boundary. Example ordinary wrapper:

```python
def _build_prompt(self, user_message, rag_context, retrieved_molecules, conversation_history=None):
    history = conversation_history if conversation_history is not None else self.conversation_history
    return prompt_builder.build_chat_prompt(
        user_message, rag_context, history=history, config=self.config,
        input_limit=self._input_limit,
    )
```

- [ ] Preserve `InputBudgetExceeded`, status reserve, exact metadata keys/field order, newest-priority/chronological output, whole-record/whole-turn omission and prompt text. Do not move model token limits, finalization or generation-intent rules.
- [ ] Run input-budget/local-cleanup tests and direct boundary tests. Check no new module imports model/app/handler and no duplicate old algorithm remains in wrappers.

## Task 4: Verification, review and release

- [ ] Full Agent + entrypoint/lifecycle/ownership/phase2-phase3 regression using existing isolated runner; report actual counts, skips and warnings.
- [ ] Run relevant five homepage Node tests and compile source through the existing safe bytecode/compile workflow. Do not bypass cleanup policy or modify production assets.
- [ ] Independent SPEC then QUALITY review; fix valid findings with RED/GREEN and rerun affected tests.
- [ ] Update `docs/AGENT_MAINTENANCE.md`, `docs/handoff/latest.md`, and `docs/handoff/remaining-through-step8.md` with evidence. Package 2 is not done merely because files moved; package 3–8 remain open.
- [ ] Explicitly stage only task files, commit, push scoped branch, create draft PR and attach it. Under default merge authorization, verify latest head/checks/review threads, then squash merge; verify merged tree. No direct main commit, deployment or model activation.
